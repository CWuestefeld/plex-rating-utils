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
            critic_rating = album.rating
            
            # Ensure we have a clean ISO string
            rel_date = album.originallyAvailableAt.isoformat() if album.originallyAvailableAt else None

            sql = """
                INSERT INTO library_albums (
                    library_id, artist_guid, rating_key, plex_guid, title, 
                    release_date, original_release_date, description, description_words, rating
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(library_id, artist_guid, rating_key) DO UPDATE SET
                    title = excluded.title,
                    release_date = excluded.release_date,
                    description = excluded.description,
                    description_words = excluded.description_words,
                    rating = excluded.rating
                -- Note: We DON'T overwrite original_release_date here so our 
                -- propagation/manual fixes stay 'sticky'.
                WHERE 
                    title != excluded.title OR 
                    release_date != excluded.release_date OR 
                    description != excluded.description OR
                    rating != excluded.rating;
            """
            self.db.execute(sql, (
                self._library_id, 
                artist_guid, 
                album.ratingKey, 
                album.guid, 
                album.title, 
                rel_date,      # release_date
                rel_date,      # original_release_date (Baseline)
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

            country_name, description = db_artist

            changes = {}

            # 2. Country Sync (The result of our Normalization work)
            # Note: p_artist.countries is a list of objects in the Plex API
            current_countries = [c.tag for c in p_artist.countries]
            if country_name:
                if country_name not in current_countries:
                    # We overwrite the country list with our single 'Gold' country
                    changes["country.value"] = country_name

            # 3. Bio/Description Sync
            if description and p_artist.summary != description:
                changes["summary.value"] = description

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
        
        for p_album in tqdm(all_albums, desc="Syncing Albums to Plex"):
            # 2. Find the 'Target' in our DB
            # We use a dictionary-style row factory for readability
            db_album = self.db.execute(
                "SELECT description, rating, release_date FROM library_albums WHERE rating_key = ?", 
                (p_album.ratingKey,)
            ).fetchone()

            if not db_album:
                continue

            # 3. Build the Delta
            changes = {}

            description, rating, release_date = db_album
            
            # Plex 'summary' vs local 'description'
            if (description and p_album.summary != description):
                changes["summary.value"] = description
                
            # Plex 'rating' vs local 'rating'
            if (rating is not None and p_album.rating != rating):
                changes["rating.value"] = rating

            # Convert the Plex object to ISO string to match the DB string exactly
            plex_date_iso = p_album.originallyAvailableAt.isoformat() if p_album.originallyAvailableAt else None
            if (release_date is not None and plex_date_iso != release_date):
                changes["originallyAvailableAt.value"] = release_date

            # 4. Execute the Push
            if changes:
                if dry_run:
                    logger.info(f"[DRY RUN] Would update {p_album.title}: {list(changes.keys())}")
                else:
                    try:
                        logger.debug(f"Updating {p_album.title}: {list(changes.keys())}")
                        p_album.edit(**changes)
                        updates_made += 1
                    except Exception as e:
                        logger.error(f"Failed to update {p_album.title}: {e}")

        logger.info(f"Sync complete. Updated {updates_made} albums.")

    def _sync_tags_to_plex(self, dry_run=True):
        logger.info("Streaming current Plex artist state, for tags...")
        all_artists = self.plex.search(libtype='artist')
        count = 0

        for p_artist in tqdm(all_artists, desc="Syncing Tags"):
            # We fetch the row; using [0] to avoid the 'tuple' error if RowFactory isn't set
            row = self.db.execute(
                "SELECT country_name FROM library_artists WHERE plex_guid = ?", 
                (p_artist.guid,)
            ).fetchone()

            if row and row[0]:
                target_country = row[0]
                existing = [c.tag for c in p_artist.countries]
                
                # If the country list doesn't match our single target country
                if len(existing) != 1 or existing[0] != target_country:
                    if dry_run:
                        logger.info(f"[DRY RUN] Tag Update {p_artist.title}: {existing} -> {target_country}")
                    else:
                        try:
                            # This single call clears old countries, sets the new one, and LOCKS it
                            p_artist.edit(**{"country.value": target_country})
                            count += 1
                        except Exception as e:
                            logger.error(f"Failed to update tags for {p_artist.title}: {e}")
        return count


    def sync_to_plex(self, dry_run=True):
        logger.info("Starting sync to Plex...")
        self._sync_artists_to_plex(dry_run)
        self._sync_albums_to_plex(dry_run)
        self._sync_tags_to_plex(dry_run)
        logger.info("Sync complete.")
        return


    ####################################################################
    # data CLEANSING

    def propagate_album_metadata(self):
        """
        Aggregates the best metadata (longest bio, highest rating, earliest date)
        across all versions of an album and broadcasts it back to all instances.
        """
        logger.info("Aggregating and broadcasting metadata across duplicate releases...")
        
        # 1. Identify groups of duplicates
        try:
            sql_find_twins = "SELECT plex_guid FROM library_albums GROUP BY plex_guid HAVING COUNT(*) > 1"
            twin_guids = [row[0] for row in self.db.execute(sql_find_twins).fetchall()]
        except Exception as e:
            logger.error(f"database error: {e}")
            return 0
        
        updated_groups = 0

        for guid in tqdm(twin_guids, desc="Propagating Duplicates"):
            # 2. Pass 1: Harvest the 'Golden' values from the group
            sql_instances = """
                SELECT rating_key, description, rating, release_date, original_release_date 
                FROM library_albums WHERE plex_guid = ?
            """

            try:
                instances = self.db.execute(sql_instances, (guid,)).fetchall()
            except Exception as e:
                logger.error(f"database error: {e}")
                break
            
            gold_desc = ""
            gold_rating = None
            gold_original_date = None

            for inst in instances:
                rating_key, description, rating, release_date, original_release_date = inst
                # Logic: Longest description wins
                if description and len(description) > len(gold_desc):
                    gold_desc = description
                
                # Logic: Highest rating wins
                if rating is not None:
                    if gold_rating is None or rating > gold_rating:
                        gold_rating = rating
                
                # Logic: Earliest date found in either date column becomes the 'Original'
                for date_val in [release_date, original_release_date]:
                    if date_val:
                        if gold_original_date is None or date_val < gold_original_date:
                            gold_original_date = date_val

            # 3. Pass 2: Broadcast the 'Golden' values to all members of the group
            if gold_desc or gold_rating is not None or gold_original_date:

                word_count = len(gold_desc.split()) if gold_desc else 0

                try:
                    # We update description, rating, and ONLY the original_release_date.
                    # We leave 'release_date' alone to preserve the specific edition's history.
                    update_sql = """
                        UPDATE library_albums 
                        SET description = ?, 
                            description_words = ?, 
                            rating = ?,
                            original_release_date = ?
                        WHERE plex_guid = ?
                    """
                    self.db.execute(update_sql, (
                        gold_desc, 
                        word_count, 
                        gold_rating, 
                        gold_original_date, 
                        guid
                    ))
                    self.db.commit()
                    updated_groups += 1

                except Exception as e:
                    logger.error(f"database error: {e}")

        logger.info(f"Propagation complete. Synchronized {updated_groups} album groups.")
        return updated_groups
