# -----------------------------------------------------------------------------
# Copyright (c) 2026 Chris Wuestefeld
# Licensed under the MIT License. See LICENSE in the project root for details.
# -----------------------------------------------------------------------------

import logging
from typing import Dict, List
import sqlite3
from tqdm import tqdm 
from plexapi.server import PlexServer
from plexapi.library import MusicSection

logger = logging.getLogger(__name__) # This will be "library_interface"

class LibraryInterface:
    def __init__(self, plex_section: MusicSection, db_connection, library_id: int):
        self.plex = plex_section
        self.db = db_connection
        self._library_id = library_id

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
            logger.info(f"Connected to Plex: '{music.title}'")
        except Exception as e:
            logger.critical(f"Plex Connection Error: {e}")
            return None

        cursor = db_connection.cursor()
        library_name = config['LIBRARY_NAME']
        
        # Check if library exists in DB
        cursor.execute("SELECT library_id, library_uuid FROM libraries WHERE library_name = ?", (library_name,))
        row = cursor.fetchone()

        if row is None:     # never seen this library before
            logger.info(f"Adding library '{library_name}' to DB...")
            cursor.execute(
                "INSERT INTO libraries (library_name, library_uuid) VALUES (?, ?)",
                (music.title, music.uuid)
            )
            db_connection.commit()
            lib_id = cursor.lastrowid
        else:   # we have used a library with this name
            lib_id, db_uuid = row[0], row[1]
            if db_uuid != music.uuid:
                logger.error(f"\nCRITICAL WARNING: Library UUID mismatch! Expected {db_uuid}, but found {music.uuid} at the Plex server")
                print("This can happen if you are running this tool against a different Plex server or library than before.")
                
                confirm = input("Do you want to update the database to use this new library UUID? (y/n): ").strip().lower()
                if confirm != 'y':
                    logger.error("Aborting due to UUID mismatch.")
                    return None
                
                try:
                    logger.warning("Updating library UUID in the database...")
                    cursor.execute("UPDATE libraries SET library_uuid = ? WHERE library_name = ?", (music.uuid, library_name))
                    db_connection.commit()
                    logger.info("Database updated successfully.")
                except sqlite3.Error as e:
                    logger.critical(f"Database error while updating library UUID: {e}")
                    return None
                db_connection.commit()

        # Return a new instance of this class
        return cls(music, db_connection, lib_id)

    ####################################################################
    # data PULL support

    def extract_mirror(self):
        """
        Performs a high-performance mirror of the Plex library into the local DB.
        Uses bulk search to minimize API overhead.
        """
        logger.info(f"Starting bulk sync for: {self.plex.title}")

        # 1. Bulk Metadata Fetch
        # We request all artists and albums in two large payloads.
        all_artists = self.plex.search(libtype='artist')
        all_albums = self.plex.search(libtype='album')

        # 2. Map Albums to Artists in memory { artist_guid: [album_objects] }
        logger.info(f"Retrieving data from Plex. This may take a few moments.\n")
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

        logger.info("Download complete.")
        logger.info(f"Processed {artist_count} artists and {album_count} albums.")

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
            logger.error(f"artist update error: {e}")
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
            logger.error(f"album update error: {e}")
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
            logger.info(f"{tag_type} tag update error: {e}")
            raise

    ####################################################################
    # data PUSH support

    def _sync_artists_to_plex(self, dry_run=True):
        """
        Streams current Plex artist state and pushes local mirror updates.
        """
        logger.info("Streaming current Plex artist state...")
        all_artists = self.plex.search(libtype='artist')
        
        updates_made = 0
        
        for p_artist in tqdm(all_artists, desc="Syncing Artists to Plex"):
            # 1. Fetch Target from DB
            db_artist = self.db.execute("""
                SELECT country_name, description 
                FROM library_artists 
                WHERE plex_guid = ?
            """, (p_artist.guid,)).fetchone()

            if not db_artist:
                continue

            changes = {}

            # 2. Country Sync (The result of our Normalization work)
            # Note: p_artist.countries is a list of objects in the Plex API
            current_countries = [c.tag for c in p_artist.countries]
            if db_artist['country_name']:
                if db_artist['country_name'] not in current_countries:
                    # We overwrite the country list with our single 'Gold' country
                    changes["country.value"] = db_artist['country_name']

            # 3. Bio/Description Sync
            if db_artist['description'] and p_artist.summary != db_artist['description']:
                changes["summary.value"] = db_artist['description']

            # 4. Push
            if changes:
                if dry_run:
                    logger.info(f"[DRY RUN] Artist {p_artist.title} changes: {list(changes.keys())}")
                else:
                    try:
                        logger.debug(f"[DRY RUN] Artist {p_artist.title} changes: {list(changes.keys())}")
                        p_artist.edit(**changes)
                        updates_made += 1
                    except Exception as e:
                        logger.error(f"Failed to update artist {p_artist.title}: {e}")

        return updates_made

    def _sync_albums_to_plex(self, dry_run=True):
        """
        Compares the local DB to the live Plex library and pushes updates.
        """
        # 1. Bulk stream the 'Reality' (Fast)
        logger.info("Streaming current Plex album state...")
        all_albums = self.plex.search(libtype='album')
        
        updates_made = 0
        
        for p_album in tqdm(all_albums, desc="Syncing to Plex"):
            # 2. Find the 'Target' in our DB
            # We use a dictionary-style row factory for readability
            db_album = self.db.execute(
                "SELECT description, rating, release_date FROM library_albums WHERE plex_guid = ?", 
                (p_album.guid,)
            ).fetchone()

            if not db_album:
                continue

            # 3. Build the Delta
            changes = {}
            
            # Plex 'summary' vs local 'description'
            if (db_album['description'] and p_album.summary != db_album['description']):
                changes["summary.value"] = db_album['description']
                
            # Plex 'rating' vs local 'rating'
            if (db_album['rating'] is not None and p_album.userRating != db_album['rating']):
                changes["userRating.value"] = db_album['rating']

            # 4. Execute the Push
            if changes:
                if dry_run:
                    logger.info(f"[DRY RUN] Would update {p_album.title}: {list(changes.keys())}")
                else:
                    try:
                        logger.debug(f"[DRY RUN] Would update {p_album.title}: {list(changes.keys())}")
                        p_album.edit(**changes)
                        updates_made += 1
                    except Exception as e:
                        logger.error(f"Failed to update {p_album.title}: {e}")

        logger.info(f"Sync complete. Updated {updates_made} albums.")

    def sync_to_plex(self, dry_run=True):
        logger.info("Starting sync to Plex...")
        self._sync_artists_to_plex(dry_run)
        self._sync_albums_to_plex(dry_run)
        return
