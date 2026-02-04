# -----------------------------------------------------------------------------
# Copyright (c) 2026 Chris Wuestefeld
# Licensed under the MIT License. See LICENSE in the project root for details.
# -----------------------------------------------------------------------------

import json
import os
import sys
import math
import time
import csv
import statistics
from plexapi.server import PlexServer
from tqdm import tqdm
import reports

# --- Config & State loading ---
APP_VERSION = "1.4.0"
CONFIG_FILE = 'config.json'
STATE_FILE = 'plex_state.json'

def load_json(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

def get_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"Configuration file '{CONFIG_FILE}' not found.")
        create = input("Would you like to create a default config file? (y/n): ").strip().lower()
        if create == 'y':
            default_config = {
                "version": APP_VERSION,
                "PLEX_URL": "http://your-server-ip:32400",
                "PLEX_TOKEN": "ENTER_TOKEN_HERE",
                "LIBRARY_NAME": "Music",
                "CONFIDENCE_C": 3.0,
                "BIAS_CRITIC": 1.5,
                "WEIGHT_CRITIC": 3.0,
                "WEIGHT_GLOBAL": 1.0,
                "DRY_RUN": True,
                "INFERRED_TAG": "Rating_Inferred",
                "DYNAMIC_PRECISION": True,
                "COOLDOWN_BATCH": 25,
                "COOLDOWN_SLEEP": 5,
                "ALBUM_INHERITANCE_GRAVITY": 0.2,
                "TRACK_INHERITANCE_GRAVITY": 0.3,  
                "BULK_ARTIST_FILENAME": "./artist_ratings.csv",
                "BULK_ALBUM_FILENAME": "./album_ratings.csv",
                "BULK_TRACK_FILENAME": "./track_ratings.csv",
                "TWIN_LOGIC": {
                    "ENABLED": True,
                    "DURATION_TOLERANCE_SEC": 5,
                    "EXCLUDE_KEYWORDS": ["live", "demo", "reprise", "instrumental", "commentary", "acoustic", "remix"],
                    "EXCLUDE_PARENTHESES": True,
                    "EXCLUDE_LIVE_ALBUMS": True,
                    "TWIN_TAG": "Twin"
                },
                "UPWARD_EXCLUSION_RULES": {
                    "ENABLED": True,
                    "MIN_DURATION_SEC": 60,
                    "KEYWORDS": ["intro", "outro", "interview", "skit", "applause", "commentary"],
                    "CASE_SENSITIVE": False
                }
            }
            try:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=2)
                print(f"\nSuccessfully created {CONFIG_FILE}.")
                print("Please open the file and update 'PLEX_URL', 'PLEX_TOKEN', and 'LIBRARY_NAME'.")
                print("\nTo find your PLEX_TOKEN:")
                print("1. Sign in to Plex in a browser.")
                print("2. Go to any media item and View XML.")
                print("3. Look for 'X-Plex-Token' in the URL or the XML content.")
                print("   (See https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)\n")
                sys.exit(0)
            except Exception as e:
                print(f"Error creating config file: {e}")
                sys.exit(1)
        else:
            print("Configuration required. Exiting.")
            sys.exit(1)
    
    cfg = load_json(CONFIG_FILE, {})
    if cfg.get('version') != APP_VERSION:
        print(f"Warning: Config file version ({cfg.get('version')}) does not match script version ({APP_VERSION}).")
    return cfg

config = get_config()
state = {}
active_library_uuid = None  # shared between load_state() and save_state()

def load_state(library):
    """Loads the state file, validating version and UUID."""
    global state, active_library_uuid
    active_library_uuid = library.uuid
    
    if not os.path.exists(STATE_FILE): return

    raw_data = load_json(STATE_FILE, {})
    
    # Detect format: New (dict with 'ratings') vs Old (flat dict)
    ratings_data = {}
    if 'ratings' in raw_data:
        loaded_uuid = raw_data.get('library_uuid')
        loaded_version = raw_data.get('version')
        
        if loaded_version != APP_VERSION:
            print(f"Note: State file version ({loaded_version}) differs from program version ({APP_VERSION}).")
            
        if loaded_uuid and loaded_uuid != library.uuid:
            print(f"\nCRITICAL WARNING: State file UUID ({loaded_uuid}) does not match current library UUID ({library.uuid}).")
            print(f"Target Library: {library.title} ({library.uuid})")
            print(f"Are you using the wrong library?")
            confirm = input("Continuing may lead to incorrect ratings. Proceed? (y/n): ").lower()
            if confirm != 'y': sys.exit(1)
            
        ratings_data = raw_data.get('ratings', {})
    else:
        print("Note: Legacy state file format detected. Will upgrade on next save.")
        ratings_data = raw_data

    # Check for old rating format (float) and migrate
    if ratings_data:
        first_key = next(iter(ratings_data))
        if isinstance(ratings_data[first_key], (int, float)):
            print("Old state file format detected. This script needs to upgrade it.")
            confirm = input("A backup will not be made. Is it OK to upgrade the file on the next save? (y/n): ").lower()
            if confirm != 'y':
                print("Cannot proceed without upgrading state file. Exiting.")
                sys.exit(1)
            
            print("Migrating state file format in memory...")
            migrated_ratings = {}
            for key, rating in tqdm(ratings_data.items(), desc="Migrating State"):
                migrated_ratings[key] = {'r': rating, 't': 0}
            state.update(migrated_ratings)
            return # We are done here

    state.update(ratings_data)


def save_state():
    """Saves the current inference registry to disk"""
    if config.get('DRY_RUN', True): return 
    
    data = {
        "version": APP_VERSION,
        "library_uuid": active_library_uuid,
        "ratings": state
    }
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def handle_pause(pbar):
    """
    Handles KeyboardInterrupt pause menu.
    Returns 'r' (resume) or 'q' (quit).
    """
    pbar.clear()
    print("\nPaused. Enter 'r' to resume or 'q' to quit.")
    while True:
        choice = input("Selection: ").lower()
        if choice == 'q':
            return 'q'
        elif choice == 'r':
            print("Resuming...")
            return 'r'

def calculate_dynamic_epsilon(item_count):
    """
    Scales the 'Close Enough' threshold. 
    Small libs: ~0.02 | 300k lib: ~0.15
    Can be disabled by setting DYNAMIC_PRECISION to false in config.
    """
    if not config.get('DYNAMIC_PRECISION', True): return 0.02
    if item_count < 1000: return 0.02
    return round(0.02 * (math.log10(item_count)-2), 3)

def is_excluded_from_averages(track, exclusion_rules):
    """Checks if a track should be excluded from upward aggregations based on duration or keywords."""
    if not exclusion_rules.get("ENABLED", False):
        return False

    # Guard clause: If the item doesn't have a duration (e.g. it's an Album), we can't exclude it based on these rules.
    if not hasattr(track, 'duration'):
        return False

    # Duration Check
    min_duration_sec = exclusion_rules.get("MIN_DURATION_SEC", 60)
    if track.duration and (track.duration // 1000) < min_duration_sec:
        return True

    # Keyword Check
    keywords = exclusion_rules.get("KEYWORDS", [])
    if not keywords or not track.title:
        return False

    title = track.title
    case_sensitive = exclusion_rules.get("CASE_SENSITIVE", False)
    if not case_sensitive:
        title = title.lower()
        keywords = [k.lower() for k in keywords]

    if any(keyword in title for keyword in keywords):
        return True

    return False

def get_library_prior(music, silent=False):
    """Calculates the Bayesian Prior using only Manual (User) ratings."""
    if not silent: print("Calculating Global Prior (Manual ratings only)...")
    all_rated = music.searchTracks(filters={'userRating>>': 0})
    manual_ratings = []
    exclusion_rules = config.get('UPWARD_EXCLUSION_RULES', {})

    for t in all_rated:
        # Gatekeeper: Exclude non-musical tracks from global average
        if is_excluded_from_averages(t, exclusion_rules):
            continue

        key = str(t.ratingKey)
        current_val = t.userRating or 0.0
        # A rating is manual if it's not in our state file, or if the value has been changed by the user
        if key not in state or abs(state[key]['r'] - current_val) > 0.01:
            if key in state: del state[key]
            manual_ratings.append(current_val)
    prior = sum(manual_ratings) / len(manual_ratings) if manual_ratings else 6.0
    return prior, len(manual_ratings)

def _clean_title(title, album_title, twin_config):
    if not title: return None
    
    title = title.lower().strip()
    album_title = album_title.lower().strip() if album_title else ""

    if twin_config.get('EXCLUDE_PARENTHESES', True):
        if any(p in title for p in "()[]") or any(p in album_title for p in "()[]"):
            return None

    # Combine keywords from Twin Logic and Upward Exclusion for comprehensive filtering
    twin_keywords = twin_config.get('EXCLUDE_KEYWORDS', [])
    upward_config = config.get('UPWARD_EXCLUSION_RULES', {})
    upward_keywords = []
    if upward_config.get('ENABLED', False):
        upward_keywords = upward_config.get('KEYWORDS', [])

    # Use a set for efficient lookup, always lowercase for matching
    all_exclude_keywords = set(k.lower() for k in twin_keywords + upward_keywords)

    # Check for whole word matches
    title_words = f" {title} "
    if any(f" {word} " in title_words for word in all_exclude_keywords):
        return None

    return title

def _clean_artist(track):
    artist = track.originalTitle or track.grandparentTitle
    if not track.originalTitle and track.grandparentTitle and 'various artists' in track.grandparentTitle.lower():
        return None
    return artist.lower().strip() if artist else None

def build_twin_clusters(music, state, twin_config):
    """Scans the library to find potential duplicate tracks ("twins") based on artist and title matching."""
    print("Building twin cluster registry...")
    registry = {}
    all_rated_tracks = music.searchTracks(filters={'userRating>>': 0})
    
    exclude_live = twin_config.get('EXCLUDE_LIVE_ALBUMS', True)

    pbar = tqdm(all_rated_tracks, desc="Scanning for twins", unit="track")
    for track in pbar:
        artist = _clean_artist(track)
        if not artist: continue
        
        title = _clean_title(track.title, track.parentTitle, twin_config)
        if not title: continue

        twin_key = (artist, title)
        key = str(track.ratingKey)
        
        track_data = {
            'item': track,
            'ratingKey': key,
            'rating': track.userRating,
            'is_manual': key not in state,
            'duration': track.duration // 1000 if track.duration else 0
        }
        
        if twin_key not in registry: registry[twin_key] = []
        registry[twin_key].append(track_data)

    clusters = [v for v in registry.values() if len(v) >= 2]
    final_clusters = []
    tolerance = twin_config.get('DURATION_TOLERANCE_SEC', 5)
    
    for cluster in tqdm(clusters, desc="Verifying clusters", unit="cluster"):
        if not cluster or not all(t['duration'] > 0 for t in cluster): continue
        
        median_duration = statistics.median([t['duration'] for t in cluster])
        filtered_cluster = [t for t in cluster if abs(t['duration'] - median_duration) <= tolerance]
        
        if exclude_live and len(filtered_cluster) >= 2:
            non_live_cluster = []
            for t in filtered_cluster:
                try:
                    # Check if the album is a Live album via subformats
                    if 'Live' in t['item'].album().subformats:
                        continue
                except Exception:
                    pass
                non_live_cluster.append(t)
            filtered_cluster = non_live_cluster

        if len(filtered_cluster) >= 2: final_clusters.append(filtered_cluster)
            
    print(f"Found {len(final_clusters)} potential twin clusters.")
    return final_clusters

def process_twins(music, state, config):
    """Phase 5: Identifies and unifies ratings for duplicate tracks."""
    twin_config = config.get('TWIN_LOGIC', {})
    if not twin_config.get('ENABLED', False):
        print("Twin Logic is disabled in config.json.")
        return 0

    print("\n--- Phase 5: Twin Logic Processing ---")
    clusters = build_twin_clusters(music, state, twin_config)
    if not clusters: return 0

    dry_run = config.get('DRY_RUN', True)
    twin_tag_name = twin_config.get('TWIN_TAG', '').strip()
    inferred_tag_name = config.get('INFERRED_TAG', '').strip()
    epsilon = calculate_dynamic_epsilon(len(music.searchTracks()))
    updated_count, batch_counter = 0, 0
    cooldown_batch = config.get('COOLDOWN_BATCH', 25)
    cooldown_sleep = config.get('COOLDOWN_SLEEP', 5)

    pbar = tqdm(clusters, desc="Processing Twin Clusters", unit="cluster")
    for cluster in pbar:
        try:
            manual_anchors = [t for t in cluster if t['is_manual']]
            target_rating, new_twin_flag = 0.0, 0

            if manual_anchors:
                target_rating = statistics.mean(t['rating'] for t in manual_anchors)
                new_twin_flag = 2
                pbar.set_postfix_str("Manual Anchor")
            else:
                target_rating = statistics.mean(t['rating'] for t in cluster)
                new_twin_flag = 1
                pbar.set_postfix_str("Inferred Consensus")

            for track_data in cluster:
                item = track_data['item']
                key = track_data['ratingKey']
                current_rating = track_data['rating'] or 0.0
                
                final_rating = current_rating if (new_twin_flag == 2 and track_data['is_manual']) else target_rating

                if abs(current_rating - final_rating) > epsilon:
                    updated_count += 1
                    batch_counter += 1
                    
                    if not dry_run:
                        item.rate(final_rating)
                        # Apply inferred tag if configured
                        if inferred_tag_name and inferred_tag_name not in [m.tag for m in item.moods]:
                            item.addMood(inferred_tag_name)
                        # Apply twin tag if configured
                        if twin_tag_name and twin_tag_name not in [m.tag for m in item.moods]:
                            item.addMood(twin_tag_name)
                
                if not dry_run:
                    state[key] = {'r': final_rating, 't': new_twin_flag}

            tqdm.write(f"\nTracks: {[t['item'].title for t in cluster]}")
            tqdm.write(f"  Ratings: {[t['rating']/2 for t in cluster]}\n  Type: {'Manual Anchor' if manual_anchors else 'Inferred'}\n  Target: {target_rating/2:.2f}\n")

            if batch_counter >= cooldown_batch:
                save_state()
                pbar.set_description(f"Twin Clusters: --pause {cooldown_sleep}s--")
                time.sleep(cooldown_sleep)
                batch_counter = 0
                pbar.set_description("Processing Twin Clusters")

        except KeyboardInterrupt:
            if handle_pause(pbar) == 'q':
                pbar.close(); save_state(); print("\n\n>>> Graceful Exit: Twin processing interrupted."); sys.exit(0)
            batch_counter = 0
        except Exception as e:
            tqdm.write(f"Error processing a twin cluster: {e}")

    if not dry_run: save_state()
    print(f"Twin Logic complete. Updated {updated_count} tracks across {len(clusters)} clusters.")
    return updated_count

def run_reconstruction(music):
    """Option 8: Rebuilds state from Artists, Albums, AND Tracks."""
    tag_name = config.get('INFERRED_TAG', "").strip()
    if not tag_name:
        print("Error: No INFERRED_TAG defined in config.")
        return

    print(f"\n--- Option 8: State Reconstruction (Mode: {'DRY RUN' if config.get('DRY_RUN', True) else 'LIVE'}) ---")
    
    restored_count = 0
    # We must search each type explicitly
    search_types = ['artist', 'album', 'track']
    
    for stype in search_types:
        tqdm.write(f"Searching for tagged {stype}s...")
        tagged_items = music.search(filters={'mood': tag_name}, libtype=stype)
        
        pbar = tqdm(tagged_items, desc=f"Restoring {stype}s", unit="item", leave=False)
        for item in pbar:
            key = str(item.ratingKey)
            if item.userRating and item.userRating > 0:
                if key not in state:
                    restored_count += 1
                    if not config.get('DRY_RUN', True): # Mark as inferred, not a twin
                        state[key] = {'r': item.userRating, 't': 0}
                    pbar.set_postfix(restored=restored_count)

    if restored_count > 0 and not config.get('DRY_RUN', True):
        save_state()
        print(f"\nSuccess: Restored {restored_count} total items to plex_state.json.")
    else:
        print(f"\nReconstruction finished. Items found: {restored_count}")

def run_tag_sync(music):
    """Synchronizes the Inferred tag based on the state file."""
    print("\n--- Admin: Synchronize Inferred Tags ---")
    tag_name = config.get('INFERRED_TAG', "").strip()
    if not tag_name:
        print("Error: No INFERRED_TAG defined in config.json. This feature is disabled.")
        return

    dry_run = config.get('DRY_RUN', True)
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"This will scan all items and add/remove the '{tag_name}' tag to match the state file.")
    confirm = input("This can be a very long process. Continue? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Operation cancelled.")
        return

    cooldown_batch = config.get('COOLDOWN_BATCH', 25)
    cooldown_sleep = config.get('COOLDOWN_SLEEP', 5)

    search_types = ['artist', 'album', 'track']

    for stype in search_types:
        pbar_desc = f"Syncing {stype}s"
        print(f"\n--- Phase: {pbar_desc} ---")

        try:
            # TODO: I think the music.search() below is wrong. We need different search calls depending on object type
            # Single-Pass Retrieval: Fetch all items of the current type in one go.
            tqdm.write(f"Fetching all {stype}s from Plex...")
            all_items = music.search(libtype=stype)

            tags_added = 0
            tags_removed = 0
            batch_counter = 0

            pbar = tqdm(all_items, desc=pbar_desc, unit="item")
            for item in pbar:
                try:
                    key = str(item.ratingKey)
                    # Avoid Redundant Tag Queries: Check moods from the retrieved item.
                    has_tag = tag_name in [m.tag for m in item.moods]
                    is_in_state = key in state

                    # Condition A: State says inferred, but tag is missing.
                    if is_in_state and not has_tag:
                        if not dry_run:
                            item.addMood(tag_name)
                        tags_added += 1
                        batch_counter += 1
                        tqdm.write(f"  {'[DRY RUN] ' if dry_run else ''}Adding tag to '{item.title}'")

                    # Condition B: State says manual (or missing), but tag is present.
                    elif not is_in_state and has_tag:
                        if not dry_run:
                            item.removeMood(tag_name)
                        tags_removed += 1
                        batch_counter += 1
                        tqdm.write(f"  {'[DRY RUN] ' if dry_run else ''}Removing tag from '{item.title}'")

                    # Performance & Safety: Cooldown pause logic.
                    if batch_counter >= cooldown_batch:
                        pbar.set_description(f"{pbar_desc} (pausing...)")
                        time.sleep(cooldown_sleep)
                        batch_counter = 0
                        pbar.set_description(pbar_desc)

                except KeyboardInterrupt:
                    # Performance & Safety: Graceful stop.
                    if handle_pause(pbar) == 'q':
                        pbar.close()
                        print("\n\n>>> Graceful Exit: Tag sync interrupted.")
                        sys.exit(0)
                    batch_counter = 0  # Reset counter on resume
                except Exception as e:
                    tqdm.write(f"Warning: Could not process item '{item.title}' ({item.ratingKey}). Error: {e}")

            pbar.close()
            print(f"Phase for {stype}s complete. Tags added: {tags_added}, Tags removed: {tags_removed}")

        except Exception as e:
            tqdm.write(f"Error during sync for {stype}s: {e}")

    print("\nTag synchronization complete.")

def run_bulk_export(music, item_type):
    """Exports Artist, Album, or Track data to a CSV file."""
    if item_type == 'artist':
        default_filename = config.get('BULK_ARTIST_FILENAME', './artist_ratings.csv')
        headers = ['ratingKey', 'artistName', 'sortName', 'albumCount', 'genres', 'userRating', 'ratingType']
        items = music.searchArtists()
        print("\n--- Export Artist Ratings ---")
    elif item_type == 'album':
        default_filename = config.get('BULK_ALBUM_FILENAME', './album_ratings.csv')
        headers = ['ratingKey', 'albumName', 'sortName', 'artistName', 'releaseYear', 'genres', 'userRating', 'ratingType']
        items = music.searchAlbums()
        print("\n--- Export Album Ratings ---")
    elif item_type == 'track':
        default_filename = config.get('BULK_TRACK_FILENAME', './track_ratings.csv')
        headers = ['ratingKey', 'trackTitle', 'trackArtist', 'albumName', 'albumArtist', 'userRating', 'ratingType']
        items = music.searchTracks()
        print("\n--- Export Track Ratings ---")
    else:
        return

    filename = input(f"Enter filename to export to [{default_filename}]: ").strip() or default_filename
    
    if os.path.exists(filename):
        overwrite = input(f"File '{filename}' already exists. Overwrite? (y/n): ").strip().lower()
        if overwrite != 'y':
            print("Export cancelled.")
            return

    try:
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            
            written_count = 0
            pbar = tqdm(items, desc=f"Exporting {item_type}s", unit="item")
            for item in pbar:
                key = str(item.ratingKey)
                rating_type = 'inferred' if key in state else 'manual'
                user_rating = (item.userRating / 2.0) if item.userRating else ''
                genres = ', '.join([g.tag for g in item.genres])

                row = []
                if item_type == 'artist':
                    album_count = len(item.albums())
                    row = [key, item.title, item.titleSort, album_count, genres, user_rating, rating_type]
                elif item_type == 'album':
                    row = [key, item.title, item.titleSort, item.parentTitle, item.year, genres, user_rating, rating_type]
                elif item_type == 'track':
                    track_artist = item.originalTitle if item.originalTitle else item.grandparentTitle
                    row = [key, item.title, track_artist, item.parentTitle, item.grandparentTitle, user_rating, rating_type]
                
                writer.writerow(row)
                written_count += 1
        
        print(f"\nSuccessfully wrote {written_count} records to '{filename}'.")

    except Exception as e:
        print(f"\nAn error occurred during export: {e}")

def run_bulk_import(music, item_type):
    """Imports Artist, Album, or Track ratings from a CSV file."""
    if item_type == 'artist':
        default_filename = config.get('BULK_ARTIST_FILENAME', './artist_ratings.csv')
        expected_headers = ['ratingKey', 'userRating', 'ratingType']
        print("\n--- Import Artist Ratings ---")
    elif item_type == 'album':
        default_filename = config.get('BULK_ALBUM_FILENAME', './album_ratings.csv')
        expected_headers = ['ratingKey', 'userRating', 'ratingType', 'releaseYear']
        print("\n--- Import Album Ratings ---")
    elif item_type == 'track':
        default_filename = config.get('BULK_TRACK_FILENAME', './track_ratings.csv')
        expected_headers = ['ratingKey', 'userRating', 'ratingType']
        print("\n--- Import Track Ratings ---")
    else:
        return

    filename = input(f"Enter filename to import from [{default_filename}]: ").strip() or default_filename

    if not os.path.exists(filename):
        print(f"Error: File '{filename}' not found.")
        return

    dry_run = config.get('DRY_RUN', True)
    tag_name = config.get('INFERRED_TAG', "").strip()
    cooldown_batch = config.get('COOLDOWN_BATCH', 25)
    cooldown_sleep = config.get('COOLDOWN_SLEEP', 5)
    batch_counter = 0
    
    examined_count = 0
    updated_count = 0

    try:
        with open(filename, 'r', newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            
            missing_headers = [h for h in expected_headers if h not in reader.fieldnames]
            if missing_headers:
                print(f"Error: CSV file is missing required columns: {', '.join(missing_headers)}")
                return

            rows = list(reader)
            
            # Optimization: Pre-fetch items if we have a significant number of rows
            plex_lookup = {}
            if len(rows) > 25:
                print(f"Pre-fetching {item_type}s from Plex to optimize import...")
                try:
                    if item_type == 'artist':
                        plex_lookup = {str(x.ratingKey): x for x in music.searchArtists()}
                    elif item_type == 'album':
                        plex_lookup = {str(x.ratingKey): x for x in music.searchAlbums()}
                    elif item_type == 'track':
                        plex_lookup = {str(x.ratingKey): x for x in music.searchTracks()}
                except Exception as e:
                    print(f"Warning: Pre-fetch failed ({e}). Falling back to individual fetches.")
                    plex_lookup = {}

            pbar = tqdm(rows, desc=f"Importing {item_type}s", unit="item")
            for row in pbar:
                try:
                    examined_count += 1
                    key = row.get('ratingKey')
                    if not key:
                        tqdm.write(f"Warning: Skipping row {examined_count + 1}, missing ratingKey.")
                        continue

                    try:
                        if plex_lookup:
                            item = plex_lookup.get(key)
                            if not item:
                                tqdm.write(f"Warning: Item with key {key} not found in library scan.")
                                continue
                        else:
                            item = music.fetchItem(int(key))

                        pbar.set_description(f"Importing {item_type}: {item.title[:20]:<20}")
                        item_was_updated = False

                        # 1. Process Rating Value
                        new_rating_10_point = None
                        new_rating_str = row.get('userRating', '').strip()
                        if new_rating_str:
                            new_rating_10_point = float(new_rating_str) * 2.0
                            new_rating_10_point = max(0.0, min(10.0, new_rating_10_point))
                        
                        current_rating = item.userRating or 0.0
                        
                        rating_changed = (
                                            (new_rating_10_point is None and current_rating != 0.0) or 
                                            (new_rating_10_point is not None and abs(current_rating - new_rating_10_point) > 0.01)
                                        )

                        # TODO! This is a hack to avoid updating ratings for now
                        # rating_changed = False

                        if rating_changed:
                            if not dry_run: item.rate(new_rating_10_point)
                            item_was_updated = True
                            tqdm.write(f"  {'[DRY RUN] ' if dry_run else ''}Rating for '{item.title}': {current_rating/2:.2f} -> {(new_rating_10_point/2 if new_rating_10_point is not None else 'Unrated')}")

                        # 2. Process Rating Type (manual/inferred)
                        new_type = row.get('ratingType', 'manual').strip().lower()
                        is_inferred = key in state
                        should_be_inferred = (new_type == 'inferred' and new_rating_10_point is not None)

                        if should_be_inferred and (not is_inferred or rating_changed):
                            if not dry_run: # Mark as inferred, not a twin
                                state[key] = {'r': new_rating_10_point, 't': 0}
                                # if tag_name: item.addMood(tag_name)
                            if not is_inferred:
                                item_was_updated = True
                                tqdm.write(f"  {'[DRY RUN] ' if dry_run else ''}Type for '{item.title}': Manual -> Inferred")
                        elif not should_be_inferred and is_inferred: # User is marking it as manual
                            if not dry_run:
                                del state[key]
                                if tag_name: item.removeMood(tag_name)
                            item_was_updated = True
                            tqdm.write(f"  {'[DRY RUN] ' if dry_run else ''}Type for '{item.title}': Inferred -> Manual")

                        # 3. Process Album Year
                        if item_type == 'album' and row.get('releaseYear', '').strip().isdigit():
                            new_year = int(row['releaseYear'])
                            if new_year != item.year:
                                if not dry_run: item.edit(originallyAvailableAt=f"{new_year}-01-01")
                                item_was_updated = True
                                tqdm.write(f"  {'[DRY RUN] ' if dry_run else ''}Year for '{item.title}': {item.year} -> {new_year}")

                        if item_was_updated:
                            updated_count += 1
                            batch_counter += 1

                        if updated_count > 0 and updated_count % 100 == 0:
                            if not dry_run: save_state()

                        if batch_counter >= cooldown_batch:
                            if not dry_run: save_state()
                            pbar.set_description(f"Importing {item_type}: --pause {cooldown_sleep}s--")
                            time.sleep(cooldown_sleep)
                            batch_counter = 0

                    except Exception as e:
                        tqdm.write(f"Warning: Failed to process item with key {key} ('{row.get('trackTitle', row.get('albumName', row.get('artistName', 'N/A')))}'). Error: {e}")
                except KeyboardInterrupt:
                    if handle_pause(pbar) == 'q':
                        pbar.close()
                        if not dry_run: save_state()
                        print("\n\n>>> Graceful Exit: Import interrupted by user.")
                        sys.exit(0)
                    batch_counter = 0 # Reset counter on resume

        if not dry_run: save_state()
        print(f"\nImport complete. Examined {examined_count} records, made {updated_count} updates.")
    except Exception as e:
        print(f"\nAn error occurred during import: {e}")

def run_cleanup(music):
    """Option 6: Undoes script effects using Shadow DB and Tag safety sweep."""
    print("\n--- Option 6: Cleanup / Undo Mode ---")
    tag_name = config.get('INFERRED_TAG', "").strip()
    cooldown_batch = config.get('COOLDOWN_BATCH', 25)
    cooldown_sleep = config.get('COOLDOWN_SLEEP', 5)
    
    # Create a static list of items to iterate so we can modify 'state' safely during the loop
    items_to_check = list(state.items())
    keys_removed_count = 0
    batch_counter = 0
    
    pbar = tqdm(items_to_check, desc="Undoing via State", unit="item")
    for key_str, stored_rating in pbar:
        try:
            try:
                item = music.fetchItem(int(key_str))
                current_rating = item.userRating or 0.0
                if abs(current_rating - stored_rating) < 0.02:
                    if not config.get('DRY_RUN', True):
                        item.rate(None)
                        if tag_name: item.removeMood(tag_name)
                        # Remove from state immediately to allow incremental saving
                        if key_str in state: del state[key_str]
                        
                        keys_removed_count += 1
                        batch_counter += 1
            except Exception: pass # Skip individual item errors
            
            # Periodic Save
            if keys_removed_count % 100 == 0 and keys_removed_count > 0:
                save_state()

            # Cooldown
            if batch_counter >= cooldown_batch:
                save_state()
                pbar.set_description(f"Undoing: --pause {cooldown_sleep}s--")
                time.sleep(cooldown_sleep)
                batch_counter = 0
                pbar.set_description("Undoing via State")
        except KeyboardInterrupt:
            if handle_pause(pbar) == 'q':
                print("\n\n>>> Graceful Exit: Process interrupted by user.")
                save_state()
                sys.exit(0)
            batch_counter = 0
        
    if not config.get('DRY_RUN', True):     # don't actually save if we're in a dry run
        save_state()

    if tag_name:
        print(f"\nPerforming safety sweep for remaining '{tag_name}' tags...")
        items_with_tag = music.search(filters={'mood': tag_name})
        batch_counter = 0 # Reset for next loop
        
        pbar_sweep = tqdm(items_with_tag, desc="Safety Sweep", unit="item")
        for item in pbar_sweep:
            try:
                current_rating = item.userRating or 0
                is_standard = (current_rating * 2) % 1 == 0
                if not config.get('DRY_RUN', True):
                    if not is_standard: item.rate(None)
                    item.removeMood(tag_name)
                    batch_counter += 1
                
                if batch_counter >= cooldown_batch:
                    pbar_sweep.set_description(f"Sweep: --pause {cooldown_sleep}s--")
                    time.sleep(cooldown_sleep)
                    batch_counter = 0
                    pbar_sweep.set_description("Safety Sweep")

            except KeyboardInterrupt:
                if handle_pause(pbar_sweep) == 'q':
                    print("\n\n>>> Graceful Exit: Process interrupted by user.")
                    save_state()
                    sys.exit(0)
                batch_counter = 0

    print("\nCleanup Complete.")

def run_verification(music):
    """Option 5: Reports discrepancies between State and Plex."""
    print("\n--- Option 5: Verification Mode ---")
    discrepancies, overrides = 0, 0
    pbar = tqdm(state.items(), desc="Verifying State", unit="item")
    for key_str, stored_rating in pbar:
        try:
            item = music.fetchItem(int(key_str))
            current_plex_rating = item.userRating or 0
            if abs(current_plex_rating - stored_rating['r']) > 0.01:
                tqdm.write(f"  [OVERRIDE] {item.title}: Script expected {stored_rating['r']/2:.2f}, found {current_plex_rating/2:.2f}")
                overrides += 1
        except: discrepancies += 1
    print(f"\nDetected Overrides: {overrides} | Orphaned: {discrepancies}")

def process_layer(label, items, global_mean, start_char="", direction="UP"):
    """
    This is the real meat here, where we do the analysis and computation
    For a given item, we have three rating values:
        - state_rating: what we recorded in our plex_state.json as the value we assigned in the last run.
        - plex_rating: what Plex thinks the current rating is.
        - inferred_rating: what we want to set the value to.

        These are the state transitions we need to make:

        A. state rating is None, plex rating is not None: This indicates a new item with a manual rating already in it.
        B. state rating is None, plex rating is None: this is a new item.
        C. state rating is not None, plex rating is not None, abs(current - new_rating) > 0.01: this is a previously-inferred rating that the user has taken manual control of.
        D. state rating is not None, plex rating is not None, abs(current - new_rating) <= 0.01, abs(current - inferred) < 0.1: this is an inferred value that has drifted slightly.
        E. state rating is not None, plex rating is not None, abs(current - new_rating) <= 0.01, abs(current - inferred) >= 0.1: this is an inferred value that has drifted significantly.
    """
    updated_count, skipped_count, hijacked_count = 0, 0, 0
    batch_counter = 0
    start_char_floor = start_char.upper() if start_char else chr(0)
    tag_name = config.get('INFERRED_TAG', "").strip()
    cooldown_batch = config.get('COOLDOWN_BATCH',25)
    cooldown_sleep = config.get('COOLDOWN_SLEEP', 5)

    # Validation of the math constant
    c_val = config.get('CONFIDENCE_C', 3.0)
    w_critic = config.get('WEIGHT_CRITIC', 3.0)
    w_global = config.get('WEIGHT_GLOBAL', 1.0)
    b_critic = config.get('BIAS_CRITIC', 1.5)
    if c_val <= 0:
        print("Error: CONFIDENCE_C must be a positive number. Defaulting to 3.0.")
        c_val = 3.0
    
    if label == 'Album':
        gravity = config.get('ALBUM_INHERITANCE_GRAVITY', 0.2)
    elif label == 'Track':
        gravity = config.get('TRACK_INHERITANCE_GRAVITY', 0.3)
    else:
        gravity = 0.0


    # Calculate threshold for this run
    epsilon = calculate_dynamic_epsilon(len(items))
    print(f"Dynamic Precision: Accepting drift up to {epsilon} stars for this {label} pass.")

    # 1. SORTING
    print(f"Sorting {label}s...")
    if label == 'Album':
        items.sort(key=lambda x: (x.parentTitle.upper() if x.parentTitle else "", x.title.upper()))
    elif label == 'Artist':
        items.sort(key=lambda x: x.title.upper())
    elif label == 'Track':
        items.sort(key=lambda x: (x.grandparentTitle.upper() if x.grandparentTitle else "", x.parentTitle.upper() if x.parentTitle else "", x.title.upper()))

    current_section = None
    pbar = tqdm(items, desc=f"Phase: {label} ({direction})", unit="item")
    
    for item in pbar:
        try:
            # Determine sorting name and display name for progress bar
            if label == 'Album': 
                sort_name = item.parentTitle or "Unknown"
                display_name = item.title[:15] # 15 chars of Album
            elif label == 'Artist': 
                sort_name = item.title or "Unknown"
                display_name = sort_name[:15] # 15 chars of Artist
            elif label == 'Track': 
                sort_name = item.grandparentTitle or "Unknown"
                display_name = (item.parentTitle or "Unknown")[:15] # 15 chars of Album
            
            # Update progress bar description with current item
            pbar.set_description(f"{label}: {display_name:<15}")

            first_char = sort_name.strip()[0].upper() if sort_name.strip() else "?"
            if first_char < start_char_floor: continue
            if first_char != current_section:
                current_section = first_char
                tqdm.write(f">>> Section: [{current_section}] ({sort_name})")

            key = str(item.ratingKey)
            plex_rating = item.userRating or 0.0
            state_rating = state.get(key) # None if Case A/B, object if C/D/E

            # --- CASE C: MANUAL HIJACK DETECTION ---
            # We thought we owned it, but the Plex value changed since our last write
            if state_rating is not None and abs(state_rating['r'] - plex_rating) > 0.01:
                if not config.get('DRY_RUN', True):
                    del state[key]
                    if tag_name:
                        item.removeMood(tag_name)
                hijacked_count += 1
                continue

            # --- CASE A: NEW ITEM WITH MANUAL RATING ---
            if state_rating is None and plex_rating > 0:
                continue

            # --- CALCULATION ---
            inferred_rating = None
            if direction == "UP":
                # 1. Gather all children
                children = item.tracks() if label == 'Album' else item.albums()
                
                # 2. FILTER: Only use children NOT in our managed state (Manual ratings)
                # This prevents the "feedback loop" where inferred ratings inform parents
                manual_children = [c for c in children if (c.userRating or 0) > 0 and str(c.ratingKey) not in state]
                
                # Apply upward exclusion rules to filter out non-musical tracks
                exclusion_rules = config.get('UPWARD_EXCLUSION_RULES', {})
                if exclusion_rules.get("ENABLED", False):
                    contributing_children = [c for c in manual_children if not is_excluded_from_averages(c, exclusion_rules)]
                    # Fallback to all manual children if filtering removed everything, but only if there were manual children to begin with
                    if not contributing_children and manual_children:
                        contributing_children = manual_children
                else:
                    contributing_children = manual_children

                sum_manual = sum(c.userRating for c in contributing_children)
                n_manual = len(contributing_children)

                # 3. Determine Informed Prior (p_i)
                # Normalize and Bias the critic rating (clamped 0-10)
                if (item.rating and item.rating > 0):
                    c_rating = min( 0, ( (item.rating + b_critic) / (10 + b_critic) ) * 10 )
                else:
                    c_rating = None
                
                if c_rating and w_critic > 0:
                    p_i = ((global_mean * w_global) + (c_rating * w_critic)) / (w_global + w_critic)
                else:
                    p_i = global_mean

                # 4. Apply Bayesian Blend using CONFIDENCE_C
                inferred_rating = ((c_val * p_i) + sum_manual) / (c_val + n_manual)

            elif direction == "DOWN":
                try:
                    parent = item.artist() if label == 'Album' else item.album()
                    if parent and parent.userRating and parent.userRating > 0:
                        parent_key = str(parent.ratingKey)
                        parent_rating = parent.userRating

                        # If parent's rating is inferred (in state), inherit directly.
                        # Otherwise, it's a manual rating, so apply gravity.
                        if parent_key in state:
                            inferred_rating = parent_rating
                        else:
                            inferred_rating = (parent_rating * (1 - gravity)) + (global_mean * gravity)
                except: continue

            # --- CASE D/E: DRIFT VS UPDATE ---
            if inferred_rating:
                delta = abs(plex_rating - inferred_rating)
                
                # Case D: Drift (Close enough, skip the expensive network/DB write)
                if state_rating is not None and delta < epsilon:
                    skipped_count += 1
                    continue
                
                # Case B/E: New or Significant Change
                if state_rating is None or delta >= 0.01: # 0.01 is just a noise floor
                    if not config.get('DRY_RUN', True):
                        item.rate(inferred_rating)
                        state[key] = {'r': inferred_rating, 't': 0} # Mark as inferred, not a twin
                        if tag_name and tag_name not in [m.tag for m in item.moods]:
                            item.addMood(tag_name)
                    updated_count += 1
                    batch_counter += 1 # Increment the "Burst" counter
            
            # Periodic Save
            if updated_count % 100 == 0 and updated_count > 0:
                save_state()

            # If we've hit a batch limit, pause to let Plex finish its disk I/O
            if batch_counter >= cooldown_batch:
                save_state()
                #tqdm.write(f"--- DB Breather: Pausing for {cooldown_sleep}s ---")
                pbar.set_description(f"{label}: --pause {cooldown_sleep}s--   ")
                time.sleep(cooldown_sleep)
                batch_counter = 0 # Reset the burst counter
                
        except KeyboardInterrupt:
            if handle_pause(pbar) == 'q':
                pbar.close()
                save_state()
                print("\n\n>>> Graceful Exit: Process interrupted by user.")
                
                opt_map = {('Album', 'UP'): 1, ('Artist', 'UP'): 2, ('Album', 'DOWN'): 3, ('Track', 'DOWN'): 4}
                opt_num = opt_map.get((label, direction), "?")
                restart_let = current_section if current_section else (start_char if start_char else "A")
                
                print(f"State saved. Next time, restart from Option {opt_num} Letter {restart_let}")
                sys.exit(0)
            batch_counter = 0

    if not config.get('DRY_RUN', True): save_state()
    print(f"Pass: {updated_count} Updated, {skipped_count} Drift-Skipped, {hijacked_count} Hijacks Resolved.")
    return updated_count

def handle_admin_menu(music):
    """Displays and handles the Admin Tools sub-menu."""
    while True:
        print("\n--- Admin Tools ---")
        print(" 1: Verify State")
        print(" 2: Cleanup/Undo")
        print(" 3: Reconstruct State")
        print(" 4: Synchronize Plex Tags")
        print(" -------------------")
        choice = input("Select Option or <Enter> to return: ").strip().lower()

        if choice == '':
            return
        elif choice == '1':
            run_verification(music)
        elif choice == '2':
            run_cleanup(music)
        elif choice == '3':
            run_reconstruction(music)
        elif choice == '4':
            run_tag_sync(music)
        else:
            print("Invalid option.")

def handle_bulk_actions_menu(music):
    """Displays and handles the Bulk Actions sub-menu."""
    while True:
        print("\n--- Bulk Actions ---")
        print(" 1: Export Artist Ratings to CSV")
        print(" 2: Export Album Ratings to CSV")
        print(" 3: Export Track Ratings to CSV")
        print(" 4: Import Artist Ratings from CSV")
        print(" 5: Import Album Ratings from CSV")
        print(" 6: Import Track Ratings from CSV")
        print(" --------------------")
        choice = input("Select Option or <Enter> to return: ").strip().lower()

        if choice == '':
            return
        elif choice == '1': run_bulk_export(music, 'artist')
        elif choice == '2': run_bulk_export(music, 'album')
        elif choice == '3': run_bulk_export(music, 'track')
        elif choice == '4': run_bulk_import(music, 'artist')
        elif choice == '5': run_bulk_import(music, 'album')
        elif choice == '6': run_bulk_import(music, 'track')
        else:
            print("Invalid option.")

def handle_reports_menu(music):
    """Displays and handles the Reports sub-menu."""
    cache = reports.LibraryCache(music)
    while True:
        print("\n--- Reports & Analytics ---")
        print(" 1: Library Coverage")
        print(" 2: Rating Histogram")
        print(" 3: Twins Inventory")
        print(" 4: Dissenter Report (Outliers)")
        print(" 5: Clear Cache")
        print(" ---------------------------")
        print("Note that these can take quite a while")
        print("to run on very large libraries.")
        choice = input("\nSelect Option or <Enter> to return: ").strip().lower()

        if choice == '': return
        elif choice == '1':
            reports.show_library_coverage(cache, state)
        elif choice == '2':
            reports.show_rating_histogram(cache, state)
        elif choice == '3':
            clusters = build_twin_clusters(cache, state, config.get('TWIN_LOGIC', {}))
            reports.show_twins_inventory(clusters)
        elif choice == '4':
            reports.show_dissenter_report(cache)
        elif choice == '5':
            cache.clear()
            print("Cache cleared.")
        else:
            print("Invalid option.")

def run_processing_phases(music, choice, start_char):
    """Runs the core Bayesian inference phases (0-4)."""
    # Establish Prior
    prior, _ = get_library_prior(music)
    
    # Define the Phases
    phases = [
        ("Album", music.searchAlbums, "UP"),
        ("Artist", music.searchArtists, "UP"),
        ("Album", music.searchAlbums, "DOWN"),
        ("Track", music.searchTracks, "DOWN")
    ]

    # Determine the workload
    if choice == 0:
        # Run everything from start to finish
        workload = phases
    elif 1 <= choice <= 4:
        # Run ONLY the selected phase
        workload = [phases[choice - 1]]
    else:
        print("Invalid processing choice.")
        return

    total_updated = 0
    initial_prior = prior

    print(f"Starting prior = {prior/2:.3f} stars")
    
    # EXECUTION LOOP
    for i, (label, fetch_func, direction) in enumerate(workload):
        # We only apply the start_char to the FIRST phase of whatever the workload is
        current_start = start_char if i == 0 else ""
        
        print(f"\n>>> Executing Option {choice if choice != 0 else i+1}: {label}-{direction}")
        print(f"Fetching {label}s")
        items = fetch_func()
        total_updated += process_layer(label, items, prior, current_start, direction)

    # If full sequence, run twin logic
    if choice == 0:
        total_updated += process_twins(music, state, config)

    final_prior, _ = get_library_prior(music, silent=True)
    print("\n" + "="*45 + "\nRUN SUMMARY\n" + "="*45)
    print(f"Total Items Updated:  {total_updated}\nStart Global Prior:   {initial_prior/2:.2f} stars")
    print(f"End Global Prior:     {final_prior/2:.2f} stars\nPrior Shift:          {(final_prior - initial_prior)/2:+.4f} stars\n" + "="*45)

def main():
    automation_choice = None
    if len(sys.argv) > 1:
        try:
            automation_choice = int(sys.argv[1])
        except ValueError:
            print(f"Invalid argument: {sys.argv[1]}. Use 0-8."); return

    try:
        plex = PlexServer(config['PLEX_URL'], config['PLEX_TOKEN'])
        music = plex.library.section(config['LIBRARY_NAME'])
    except Exception as e:
        print(f"Plex Connection Error: {e}"); return

    load_state(music)

    # --- AUTOMATION MODE ---
    # For backward compatibility, run old numeric options directly
    if automation_choice is not None:
        print(f"Running in automation mode for option: {automation_choice}")
        choice = automation_choice
        
        if 0 <= choice <= 4:
            run_processing_phases(music, choice, start_char="")
        elif choice == 5:
            process_twins(music, state, config)
            save_state()
        # Shifted old options for automation
        elif choice == 6: run_verification(music)
        elif choice == 7: run_cleanup(music)
        elif choice == 9: run_reconstruction(music)
        else:
            print(f"Invalid automation option: {choice}")
        return

    # --- INTERACTIVE MODE ---
    while True:
        print(f"\n======= Bayesian Music Rating Engine (v{APP_VERSION}) =======")
        print(   "-------  Copyright (c) 2026 Chris Wuestefeld  -------\n")
        print(" 0: FULL SEQUENCE (Runs Options 1-5)")
        print(" ----------------------------------------")
        print(" 1: Album-Up   (Track Ratings -> Albums)")
        print(" 2: Artist-Up  (Album Ratings -> Artists)")
        print(" 3: Album-Down (Artist Ratings -> Albums)")
        print(" 4: Track-Down (Album Ratings -> Tracks)")
        print(" 5: Twin Logic (Unify Duplicate Tracks)")
        print(" ----------------------------------------")
        print(" A: Admin Tools")
        print(" B: Bulk Actions")
        print(" R: Reports")
        print(" ----------------------------------------")
        print(" X: eXit")
        
        choice_str = input("\nSelect Option: ").strip().upper()

        if choice_str == 'X':
            break # Exit the main loop
        
        if choice_str == 'A':
            handle_admin_menu(music)
            continue # Go back to main menu
        
        if choice_str == 'B':
            handle_bulk_actions_menu(music)
            continue # Go back to main menu

        if choice_str == 'R':
            handle_reports_menu(music)
            continue

        try:
            # Default to 0 (FULL SEQUENCE) if user just hits enter
            choice = int(choice_str or 0)
            if not (0 <= choice <= 5):
                print("Invalid choice. Please select from 0-5, A, B, or X.")
                continue
        except ValueError:
            print("Invalid choice. Please select from 0-5, A, B, or X.")
            continue
        
        if 0 <= choice <= 4:
            start_char = input("Start Artist Letter (Empty for ALL): ") or ""
            run_processing_phases(music, choice, start_char)
        elif choice == 5:
            process_twins(music, state, config)
            save_state()

if __name__ == "__main__":
    main()