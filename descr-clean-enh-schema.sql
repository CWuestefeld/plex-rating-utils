-- ==========================================================
-- 1. SYSTEM & VERSIONING
-- ==========================================================
CREATE TABLE IF NOT EXISTS system_metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS libraries (
    library_id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_uuid TEXT NOT NULL,      
    library_name TEXT NOT NULL COLLATE NOCASE,
    UNIQUE(library_uuid, library_name)
);

CREATE TABLE IF NOT EXISTS sources (
    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    base_url TEXT,
    scraper_config_json TEXT           -- For Regex/LLM prompts
);

-- ==========================================================
-- 2. TAXONOMY & MAPPING
-- ==========================================================
CREATE TABLE IF NOT EXISTS taxonomy_tags (
    library_id INTEGER NOT NULL,
    tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_name TEXT NOT NULL COLLATE NOCASE,
    tag_type TEXT CHECK(tag_type IN ('Genre', 'Style', 'Country')),
    is_canonical INTEGER DEFAULT 0,
    FOREIGN KEY (library_id) REFERENCES libraries(library_id)
);

-- (10) Targeted Scrapping: Map Sources to specific Genres
CREATE TABLE IF NOT EXISTS source_genres (
    source_id INTEGER NOT NULL,
    genre_tag_id INTEGER NOT NULL,
    PRIMARY KEY (source_id, genre_tag_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (genre_tag_id) REFERENCES taxonomy_tags(tag_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS tag_map (
    library_id INTEGER NOT NULL,
    raw_tag_name TEXT NOT NULL COLLATE NOCASE,
    tag_type TEXT NOT NULL,            -- Explicitly typed per (3)
    canonical_tag_id INTEGER NOT NULL,
    PRIMARY KEY (library_id, raw_tag_name, tag_type),
    FOREIGN KEY (library_id) REFERENCES libraries(library_id),
    FOREIGN KEY (canonical_tag_id) REFERENCES taxonomy_tags(tag_id)
) WITHOUT ROWID;

-- ==========================================================
-- 3. THE LIBRARY MIRROR (Clustered by Artist)
-- ==========================================================
CREATE TABLE IF NOT EXISTS library_artists (
    library_id INTEGER NOT NULL,
    plex_guid TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    country_name TEXT, 
    description TEXT, 
    description_words INTEGER DEFAULT 0, -- Quick flag for Auditor logic
    rating real,
    sync_status TEXT DEFAULT 'pending', -- 'pending', 'matched', 'ignored'
    PRIMARY KEY (library_id, plex_guid),
    FOREIGN KEY (library_id) REFERENCES libraries(library_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS library_albums (
    library_id INTEGER NOT NULL,
    rating_key INTEGER NOT NULL, -- The specific library item ID
    artist_guid TEXT NOT NULL,
    plex_guid TEXT NOT NULL,    -- higher-level 
    title TEXT NOT NULL COLLATE NOCASE,
    release_date DATE,
    original_release_date DATE, 
    description TEXT, 
    description_words INTEGER DEFAULT 0, -- Quick flag for Auditor logic
    rating real,
    -- Clustered by artist for fast discography matching
    PRIMARY KEY (library_id, artist_guid, rating_key),
    FOREIGN KEY (library_id, artist_guid) REFERENCES library_artists(library_id, plex_guid)
) WITHOUT ROWID;

-- Associative table for linking tags to artists/albums
CREATE TABLE IF NOT EXISTS item_tags (
    item_guid TEXT NOT NULL,           -- This is the plex_guid from library_artists or library_albums
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (item_guid, tag_id),
    FOREIGN KEY (tag_id) REFERENCES taxonomy_tags(tag_id)
) WITHOUT ROWID;

-- ==========================================================
-- 4. SOURCE DATA (The Shadow World)
-- ==========================================================
CREATE TABLE IF NOT EXISTS source_artists (
    source_id INTEGER NOT NULL,
    source_artist_id TEXT NOT NULL,    -- Artist ID from the web source
    name TEXT NOT NULL COLLATE NOCASE,
    country_name TEXT, 
    source_url TEXT,                   -- Kept for quick "New Album" checks
    PRIMARY KEY (source_id, source_artist_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS source_albums (
    source_id INTEGER NOT NULL,
    source_artist_id TEXT NOT NULL,
    source_album_id TEXT NOT NULL,
    title TEXT NOT NULL COLLATE NOCASE,
    release_date DATE,
    source_url TEXT,
    consensus_rating REAL,
    PRIMARY KEY (source_id, source_artist_id, source_album_id),
    FOREIGN KEY (source_id, source_artist_id) REFERENCES source_artists(source_id, source_artist_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS source_descriptions (
    source_id INTEGER NOT NULL,
    entity_type TEXT CHECK(entity_type IN ('Artist', 'Album')),
    parent_entity_id TEXT NOT NULL,    -- source_artist_id or source_album_id
    raw_text TEXT,
    rating REAL,                       -- Individual user/critic rating
    is_processed INTEGER DEFAULT 0,    -- 1 if LLM has summarized this
    scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (parent_entity_id, entity_type),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

-- ==========================================================
-- 5. IDENTITY LINKS
-- ==========================================================
CREATE TABLE IF NOT EXISTS identity_links (
    library_id INTEGER NOT NULL,
    plex_guid TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    source_artist_id TEXT NOT NULL,
    confidence_score REAL,
    PRIMARY KEY (library_id, plex_guid, source_id),
    FOREIGN KEY (library_id, plex_guid) REFERENCES library_artists(library_id, plex_guid),
    FOREIGN KEY (source_id, source_artist_id) REFERENCES source_artists(source_id, source_artist_id)
) WITHOUT ROWID;

-- ==========================================================
-- 6. INDEXES
-- ==========================================================
-- For faster name-based lookups and matching
CREATE INDEX IF NOT EXISTS idx_library_artists_name ON library_artists(name);
CREATE INDEX IF NOT EXISTS idx_source_artists_name ON source_artists(name);

-- For quickly finding album twins
CREATE INDEX IF NOT EXISTS idx_source_albums_plex_guid ON library_albums(plex_guid);

-- For finding all raw tags that map to a canonical tag
CREATE INDEX IF NOT EXISTS idx_tag_map_canonical_id ON tag_map(canonical_tag_id);

-- For quickly finding all external links for a given Plex item
CREATE INDEX IF NOT EXISTS idx_identity_links_plex_guid ON identity_links(plex_guid);

