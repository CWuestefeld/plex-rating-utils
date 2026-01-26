import logging
from typing import Dict, List
import sqlite3
from tqdm import tqdm 
from plexapi.server import PlexServer
from plexapi.library import MusicSection

class LibraryInterface:
    def __init__(self, plex_section: MusicSection, db_connection, library_id: int):
        self.plex = plex_section
        self.db = db_connection
        self._library_id = library_id
        self.logger = logging.getLogger("LibraryInterface")

    @property
    def library_id(self) -> int:
        """The unique ID for this library in the local database."""
        return self._library_id

    @classmethod
    def initialize_interface(cls, config, db_connection):
        """
        Factory Method: Handles the Plex connection, UUID validation, 
        and DB record creation. Returns an instance of LibraryInterface.
        """
        try:
            plex_svr = PlexServer(config['PLEX_URL'], config['PLEX_TOKEN'])
            music = plex_svr.library.section(config['LIBRARY_NAME'])
            print(f"Connected to Plex: '{music.title}'")
        except Exception as e:
            print(f"Plex Connection Error: {e}")
            return None

        cursor = db_connection.cursor()
        library_name = config['LIBRARY_NAME']
        
        # Check if library exists in DB
        cursor.execute("SELECT library_id, library_uuid FROM libraries WHERE library_name = ?", (library_name,))
        row = cursor.fetchone()

        if row is None:     # never seen this library before
            print(f"Adding library '{library_name}' to DB...")
            cursor.execute(
                "INSERT INTO libraries (library_name, library_uuid) VALUES (?, ?)",
                (music.title, music.uuid)
            )
            db_connection.commit()
            lib_id = cursor.lastrowid
        else:   # we have used a library with this name
            lib_id, db_uuid = row[0], row[1]
            if db_uuid != music.uuid:
                print("\nCRITICAL WARNING: Library UUID mismatch!")
                print(f"  - The database expects a library with UUID: {db_uuid}")
                print(f"  - The connected Plex library '{music.title}' has UUID: {music.uuid}")
                print("This can happen if you are running this tool against a different Plex server or library than before.")
                
                confirm = input("Do you want to update the database to use this new library UUID? (y/n): ").strip().lower()
                if confirm != 'y':
                    print("Aborting due to UUID mismatch.")
                    return None
                
                try:
                    print("Updating library UUID in the database...")
                    cursor.execute("UPDATE libraries SET library_uuid = ? WHERE library_name = ?", (music.uuid, library_name))
                    db_connection.commit()
                    print("Database updated successfully.")
                except sqlite3.Error as e:
                    print(f"Database error while updating library UUID: {e}")
                    return None
                db_connection.commit()

        # Return a new instance of this class
        return cls(music, db_connection, lib_id)

    def extract_mirror(self):
        """
        Performs a high-performance mirror of the Plex library into the local DB.
        Uses bulk search to minimize API overhead.
        """
        self.logger.info(f"Starting bulk sync for: {self.plex.title}")

        # 1. Bulk Metadata Fetch
        # We request all artists and albums in two large payloads.
        all_artists = self.plex.search(libtype='artist')
        all_albums = self.plex.search(libtype='album')

        # 2. Map Albums to Artists in memory { artist_guid: [album_objects] }
        print(f"Retrieving data from Plex. This may take a few moments.\n")
        album_map: Dict[str, List] = {}
        for album in all_albums:
            # album.parentGuid is the unique anchor for the Artist
            album_map.setdefault(album.parentGuid, []).append(album)

        # 3. Process and Insert
        print("writing to database")
        artist_count = 0
        album_count = 0
        for artist in tqdm(all_artists, desc="Syncing Library", unit="artist"):
            artist_count += 1
            self._upsert_artist(artist)
            self._harvest_tags(artist.guid, artist.genres, 'Genre')
            self._harvest_tags(artist.guid, artist.styles, 'Style')
            self._harvest_tags(artist.guid, artist.countries, 'Country')
            
            # Use the memory map to get this artist's albums without a new API call
            artist_albums = album_map.get(artist.guid, [])
            for album in artist_albums:
                album_count += 1
                self._upsert_album(artist.guid, album)
                self._harvest_tags(album.guid, album.genres, 'Genre')
                self._harvest_tags(album.guid, album.styles, 'Style')
            
            self.db.commit()

        self.logger.info("Download complete.")
        self.logger.info(f"Processed {artist_count} artists and {album_count} albums.")

    def _upsert_artist(self, artist):
        try:
            # We grab the first country tag as our raw 'country_name' for later cleaning
            country = artist.countries[0].tag if artist.countries else None
            description = artist.summary
            word_count = len(description.split()) if description else 0
            
            sql = """
                INSERT INTO library_artists (library_id, plex_guid, name, country_name, description, description_words, sync_status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(library_id, plex_guid) DO UPDATE SET
                    name = excluded.name,
                    country_name = excluded.country_name,
                    description = excluded.description,
                    description_words = excluded.description_words
                WHERE
                    name != excluded.name OR
                    country_name != excluded.country_name OR
                    description != excluded.description OR
                    description_words != excluded.description_words
            """
            self.db.execute(sql, (
                self._library_id, artist.guid, artist.title, country, description, word_count
            ))
        except Exception as e:
            print(f"artist error: {e}")
            raise

    def _upsert_album(self, artist_guid, album):
        try:
            description = album.summary
            word_count = len(description.split()) if description else 0
            critic_rating = album.rating  # Plex critic rating is 0-10 float

            sql = """
                INSERT INTO library_albums (library_id, artist_guid, plex_guid, title, release_date, description, description_words, rating)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(library_id, artist_guid, plex_guid) DO UPDATE SET
                    title = excluded.title,
                    release_date = excluded.release_date,
                    description = excluded.description,
                    description_words = excluded.description_words,
                    rating = excluded.rating
                WHERE 
                    title != excluded.title OR 
                    release_date != excluded.release_date OR 
                    description != excluded.description OR
                    rating != excluded.rating;
            """
            self.db.execute(sql, (
                self._library_id, 
                artist_guid, 
                album.guid, 
                album.title, 
                album.originallyAvailableAt,
                description,
                word_count,
                critic_rating
            ))
        except Exception as e:
            print(f"album error: {e}")
            raise

    def _harvest_tags(self, item_guid: str, tags: list, tag_type: str):
        """Standardizes and links Genres, Styles, and Countries found in the library."""
        if not tags:
            return
        try:
            for tag in tags:
                # 1. Ensure tag exists in the taxonomy
                self.db.execute("""
                    INSERT OR IGNORE INTO taxonomy_tags (library_id, tag_name, tag_type, is_canonical)
                    VALUES (?, ?, ?, 0)
                """, (self._library_id, tag.tag, tag_type))

                # 2. Link the tag to the item
                cursor = self.db.execute(
                    "SELECT tag_id FROM taxonomy_tags WHERE tag_name = ? AND tag_type = ? AND library_id = ?",
                    (tag.tag, tag_type, self._library_id)
                )
                row = cursor.fetchone()
                if row:
                    tag_id = row[0]
                    self.db.execute("""
                        INSERT OR IGNORE INTO item_tags (item_guid, tag_id)
                        VALUES (?, ?)
                    """, (item_guid, tag_id))
        except Exception as e:
            print(f"{tag_type} tag error: {e}")
            raise
