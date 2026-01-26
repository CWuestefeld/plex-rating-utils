import logging
import json 
import sqlite3
from pathlib import Path
from tqdm import tqdm 
from thefuzz import process

class CountryManager:
    # Path to the reference JSON file
    ISO_DATA_FILE = Path(__file__).parent / "country-names-and-codes.json"

    def __init__(self, db_connection, library_id):
        self.db = db_connection
        self.library_id = library_id
        self.logger = logging.getLogger("CountryManager")
        
        # Internal reference map will be loaded on first use.
        self._reference_map = None

    def _load_iso_data(self):
        """Builds a case-insensitive lookup map from the internal JSON file."""
        ref_map = {}
        try:
            with open(self.ISO_DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            for entry in data:
                name = entry['Name']
                code = entry['Code']
                
                # Map both Name and Code to the "Gold" Name
                ref_map[name.lower()] = name
                ref_map[code.lower()] = name
                
            return ref_map
        except FileNotFoundError:
            self.logger.error(f"ISO Data file not found: {self.ISO_DATA_FILE}")
            raise

    def _get_unmapped_strings(self):
        """Identifies unique country strings in library_artists that lack a mapping."""
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
            self.logger.debug("Loading ISO country reference data.")
            print("Loading ISO country reference data.")
            self._reference_map = self._load_iso_data()

        clean_raw = raw_string.strip().lower()

        # 1. Exact/Alias Match (O(1) lookup)
        if clean_raw in self._reference_map:
            return self._reference_map[clean_raw]

        # 2. Fuzzy Match (Handles typos like 'Unite States')
        # We check against the unique Gold names (values of the map)
        canonical_names = list(set(self._reference_map.values()))
        match, score = process.extractOne(raw_string, canonical_names)
        
        if score >= 90:
            return match

        return None

    def _commit_mapping(self, raw_string, canonical_name):
        """Records the resolution in taxonomy_tags and tag_map."""
        # 1. Ensure the "Gold" name exists as a canonical tag
        self.db.execute("""
            INSERT OR IGNORE INTO taxonomy_tags (library_id, tag_name, tag_type, is_canonical)
            VALUES (?, ?, 'Country', 1)
        """, (self.library_id, canonical_name))

        # 2. Get the tag_id for the gold version
        cursor = self.db.execute(
            "SELECT tag_id FROM taxonomy_tags WHERE tag_name = ? AND library_id = ?",
            (canonical_name, self.library_id)
        )
        tag_id = cursor.fetchone()[0]

        # 3. Map the original raw_string to that tag_id
        self.db.execute("""
            INSERT OR IGNORE INTO tag_map (library_id, raw_tag_name, tag_type, canonical_tag_id)
            VALUES (?, ?, 'Country', ?)
        """, (self.library_id, raw_string, tag_id))
        
        self.db.commit()

    def resolve_countries(self):
        """Main execution loop for cleaning country data."""
        orphans = self._get_unmapped_strings()
        if not orphans:
            self.logger.info("No unmapped countries found.")
            print("No unmapped countries found.")
            return

        ai_candidates = []
        resolved_count = 0

        for raw in tqdm(orphans, desc="Normalizing Countries"):
            canonical = self._get_canonical_name(raw)
            
            if canonical:
                print(f"resolved mapping for '{raw}' to '{canonical}'")
                self._commit_mapping(raw, canonical)
                resolved_count += 1
            else:
                ai_candidates.append(raw)

        self.logger.info(f"Resolved {resolved_count} countries. {len(ai_candidates)} forwarded to AI.")
        print(f"Resolved {resolved_count} countries. {len(ai_candidates)} forwarded to AI.")

        # Future Phase: Handle remaining semantic mismatches
        if ai_candidates:
            self._resolve_with_ai(ai_candidates)

    def _resolve_with_ai(self, candidates):
        """Placeholder for Gemini semantic resolution."""
        # TODO: Implement batch processing with Gemini 1.5 Flash
        pass

    def _update_artist_countries(self):
        """Updates the library_artists mirror with canonical country names."""
        self.logger.info("Applying country normalization to library artist mirror...")
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
        """Updates item_tags to point to canonical country tag IDs."""
        self.logger.info("Synchronizing item_tags with normalized country mappings...")
        sql = """
            UPDATE item_tags
            SET tag_id = (
                SELECT m.canonical_tag_id
                FROM tag_map m
                JOIN taxonomy_tags t_old ON m.raw_tag_name = t_old.tag_name
                WHERE t_old.tag_id = item_tags.tag_id
                  AND t_old.tag_type = 'Country'
                  AND m.library_id = ?
            )
            WHERE EXISTS (
                SELECT 1
                FROM tag_map m
                JOIN taxonomy_tags t_old ON m.raw_tag_name = t_old.tag_name
                WHERE t_old.tag_id = item_tags.tag_id
                  AND t_old.tag_type = 'Country'
                  AND m.library_id = ?
            )
        """
        result = self.db.execute(sql, (self.library_id, self.library_id))
        return result.rowcount

    def _cleanup_orphan_tags(self):
        """Deletes non-canonical country tags that are no longer referenced."""
        self.logger.info("Cleaning up orphaned, non-canonical country tags...")
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
        self.logger.info("Starting country data normalization transaction.")
        try:
            artists_updated = self._update_artist_countries()
            self.logger.info(f"Updated {artists_updated} artists with normalized country names.")
            tags_remapped = self._update_item_tag_links()
            self.logger.info(f"Re-mapped {tags_remapped} entries in item_tags.")
            tags_cleaned = self._cleanup_orphan_tags()
            self.logger.info(f"Cleaned up {tags_cleaned} orphaned country tags.")
            self.db.commit()
            self.logger.info("Country normalization transaction committed successfully.")
        except Exception as e:
            self.logger.error(f"Country normalization failed. Rolling back transaction. Error: {e}")
            self.db.rollback()
            raise
