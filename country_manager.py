# -----------------------------------------------------------------------------
# Copyright (c) 2026 Chris Wuestefeld
# Licensed under the MIT License. See LICENSE in the project root for details.
# -----------------------------------------------------------------------------

import logging
import json 
import sqlite3
from pathlib import Path
from tqdm import tqdm 
from thefuzz import process

logger = logging.getLogger(__name__) # This will be "country_manager"

class CountryManager:
    # Path to the reference JSON file
    ISO_DATA_FILE = Path(__file__).parent / "country-names-and-codes.json"

    def __init__(self, db_connection, library_id):
        self.db = db_connection
        self.library_id = library_id
        
        # Internal reference map will be loaded on first use.
        self._reference_map = None

    def _load_iso_data(self):
        """Builds a case-insensitive lookup map from the internal JSON file."""
        ref_map = {}
        gold_names = {} # Internal temporary map: { "US": "United States" }

        try:
            with open(self.ISO_DATA_FILE, 'r', encoding='utf-8') as f:
                logger.debug("Loading ISO country reference data.")
                data = json.load(f)

            # PASS 1: Find the "Gold" (preferred) name for every ISO Code
            for entry in data:
                code = entry['Code']
                name = entry['Name']
                pref = entry.get('Preference', 'preferred')
                
                # If this is the preferred name (or the first one we find for this code),
                # set it as the Gold standard for this ISO code.
                if pref == 'preferred' or code not in gold_names:
                    gold_names[code] = name

            # PASS 2: Build the lookup map where every name/code points to the Gold name
            for entry in data:
                code = entry['Code']
                name = entry['Name']
                pref = entry.get('Preference', 'preferred')
                is_preferred = (pref == 'preferred')

                # We use the gold_names map to find the REAL canonical name
                canonical_gold = gold_names[code]
                
                meta = {"canonical": canonical_gold, "is_preferred": is_preferred}
                
                # Now, 'united states of america' (non-preferred) 
                # will still have 'United States' as its canonical field.
                ref_map[name.lower()] = meta
                ref_map[code.lower()] = meta
                
            return ref_map
        
        except FileNotFoundError:
            logger.error(f"ISO Data file not found: {self.ISO_DATA_FILE}")
            raise


    def _get_unmapped_strings(self):
        """Identifies unique country strings in library_artists that lack a mapping."""
        logger.debug("getting unmapped strings...")
        query = """
            SELECT DISTINCT country_name 
            FROM library_artists 
            WHERE country_name IS NOT NULL 
            AND country_name NOT IN (
                SELECT raw_tag_name 
                FROM tag_map 
                WHERE tag_type='Country' AND library_id = ?
            )
        """
        rows = self.db.execute(query, (self.library_id,)).fetchall()
        return [row[0] for row in rows]

    def _get_canonical_name(self, raw_string):
        """
        Attempts to resolve a raw string using exact matching or fuzzy logic.
        Returns the 'Gold' name if found, otherwise None.
        """
        # Lazy-load the reference map on first use
        if self._reference_map is None:
            logger.debug("Loading ISO country reference data.")
            self._reference_map = self._load_iso_data()

        clean_raw = raw_string.strip().lower()

        # 1. Exact/Alias Match (O(1) lookup)
        if clean_raw in self._reference_map:
                mapping = self._reference_map[clean_raw]
                # If this specific entry is preferred, return it.
                # If not, we still return the 'canonical' string, because 
                # that is the 'Gold' name we want in our DB.
                return mapping["canonical"]

        # 2. Fuzzy Match (Handles typos like 'Unite States')
        # We fuzzy match against EVERY key in our map (Names, Codes, and Synonyms)
        all_known_keys = list(self._reference_map.keys())
        match_key, score = process.extractOne(clean_raw, all_known_keys)
    
        if score >= 90:
            # We found a hit on a known synonym/name/code!
            # Now we "hop" from that hit to the canonical version.
            return self._reference_map[match_key]["canonical"]

        return None

    def _commit_mapping(self, raw_string, canonical_name):
        """Records the resolution in taxonomy_tags and tag_map."""
        # 1. Ensure the "Gold" name exists as a canonical tag
        logger.debug("committing tag mapping...")
        logger.debug(f"...inserting tag {canonical_name}")
        self.db.execute("""
            INSERT OR IGNORE INTO taxonomy_tags (library_id, tag_name, tag_type, is_canonical)
            VALUES (?, ?, 'Country', 1)
        """, (self.library_id, canonical_name))

        # 2. Get the tag_id for the gold version
        logger.debug("...getting canonical tag id...")
        cursor = self.db.execute(
            "SELECT tag_id FROM taxonomy_tags WHERE tag_name = ? AND library_id = ?",
            (canonical_name, self.library_id)
        )
        tag_id = cursor.fetchone()[0]

        # 3. Map the original raw_string to that tag_id
        logger.debug(f"...inserting map {raw_string} -> {tag_id}")
        self.db.execute("""
            INSERT OR IGNORE INTO tag_map (library_id, raw_tag_name, tag_type, canonical_tag_id)
            VALUES (?, ?, 'Country', ?)
        """, (self.library_id, raw_string, tag_id))
        
        self.db.commit()

    def resolve_countries(self):
        """Main execution loop for cleaning country data."""
        orphans = self._get_unmapped_strings()
        if not orphans:
            logger.info("No unmapped countries found.")
            print("No unmapped countries found.")
            return

        ai_candidates = []
        resolved_count = 0

        for raw in tqdm(orphans, desc="Normalizing Countries"):
            canonical = self._get_canonical_name(raw)
            
            if canonical:
                logger.info(f"resolved mapping for '{raw}' to '{canonical}'")
                self._commit_mapping(raw, canonical)
                resolved_count += 1
            else:
                ai_candidates.append(raw)

        logger.info(f"Resolved {resolved_count} countries. {len(ai_candidates)} forwarded to AI (TODO).")

        # Future Phase: Handle remaining semantic mismatches
        if ai_candidates:
            self._resolve_with_ai(ai_candidates)

    def _resolve_with_ai(self, candidates):
        """Placeholder for Gemini semantic resolution."""
        # TODO: Implement batch processing with Gemini 1.5 Flash
        pass

    def _update_artist_countries(self):
        """Updates the library_artists mirror with canonical country names."""
        logger.info("Applying country normalization to library artist mirror...")
        sql = """
            UPDATE library_artists
            SET country_name = (
                SELECT t.tag_name 
                FROM tag_map m
                JOIN taxonomy_tags t ON m.canonical_tag_id = t.tag_id
                WHERE m.raw_tag_name = library_artists.country_name
                AND m.library_id = ?
            )
            WHERE library_artists.library_id = ? AND EXISTS (
                SELECT 1 FROM tag_map m 
                WHERE m.raw_tag_name = library_artists.country_name
                AND m.library_id = ?
            )
        """
        result = self.db.execute(sql, (self.library_id, self.library_id, self.library_id))
        return result.rowcount

    def _update_item_tag_links(self):
        """Updates item_tags to point to canonical country tag IDs without collisions."""
        logger.info("Merging item_tags to canonical country mappings...")
        
        # 1. INSERT the new canonical relationships. 
        # 'OR IGNORE' handles cases where the canonical link already exists.
        insert_sql = """
            INSERT OR IGNORE INTO item_tags (item_guid, tag_id)
            SELECT it.item_guid, m.canonical_tag_id
            FROM item_tags it
            JOIN taxonomy_tags t_old ON it.tag_id = t_old.tag_id
            JOIN tag_map m ON t_old.tag_name = m.raw_tag_name
            WHERE t_old.tag_type = 'Country'
            AND m.library_id = ?
            AND t_old.tag_id != m.canonical_tag_id
        """
        
        # 2. DELETE the old "messy" relationships.
        delete_sql = """
            DELETE FROM item_tags
            WHERE EXISTS (
                SELECT 1 FROM taxonomy_tags t
                JOIN tag_map m ON t.tag_name = m.raw_tag_name
                WHERE t.tag_id = item_tags.tag_id
                AND t.tag_type = 'Country'
                AND m.library_id = ?
                AND t.tag_id != m.canonical_tag_id
            )
        """

        # Execute inside a transaction
        cursor = self.db.cursor()
        cursor.execute(insert_sql, (self.library_id,))
        inserted = cursor.rowcount
        
        cursor.execute(delete_sql, (self.library_id,))
        deleted = cursor.rowcount
        
        logger.info(f"Merged {inserted} tags and removed {deleted} legacy links.")
        return inserted

    def _cleanup_orphan_tags(self):
        """Deletes non-canonical country tags that are no longer referenced."""
        logger.info("Cleaning up orphaned, non-canonical country tags...")
        sql = """
            DELETE FROM taxonomy_tags 
            WHERE is_canonical = 0 
              AND tag_type = 'Country'
              AND library_id = ?
              AND tag_id NOT IN (SELECT DISTINCT tag_id FROM item_tags)
        """
        result = self.db.execute(sql, (self.library_id,))
        return result.rowcount

    def apply_normalization(self):
        """
        Applies all country normalization steps within a single database transaction.
        1. Updates artist country names to canonical form.
        2. Re-links item tags to point to canonical country tags.
        3. Deletes unused, non-canonical country tags.
        """
        logger.info("Starting country data normalization transaction.")
        try:
            with self.db:
                artists_updated = self._update_artist_countries()
                logger.info(f"Updated {artists_updated} artists with normalized country names.")
                tags_remapped = self._update_item_tag_links()
                logger.info(f"Re-mapped {tags_remapped} entries in item_tags.")
                tags_cleaned = self._cleanup_orphan_tags()
                logger.info(f"Cleaned up {tags_cleaned} orphaned country tags.")
                logger.info("Country normalization transaction committed successfully.")
        except Exception as e:
            logger.error(f"Country normalization failed. Rolling back transaction. Error: {e}")
            self.db.rollback()
            raise
