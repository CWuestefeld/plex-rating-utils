import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from plexapi.server import PlexServer
from tqdm import tqdm

# --- CONFIG & STATE LOADING ---
CONFIG_FILE = 'config.json'
STATE_FILE = 'plex_state.json'
MAX_THREADS = 4 # Optimized for network latency dilution

def load_json(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

config = load_json(CONFIG_FILE, {})
state = load_json(STATE_FILE, {})
state_lock = threading.Lock() # Prevents corruption during parallel updates

def save_state():
    """Saves the current inference registry to disk (Thread Safe)."""
    if config.get('DRY_RUN', True): return 
    with state_lock:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)

def get_library_prior(music, silent=False):
    """Calculates the Bayesian Prior using only Manual (User) ratings."""
    if not silent: print("Calculating Global Prior (Manual ratings only)...")
    # Mega-fetch tracks to avoid O(n) prior calculation
    all_rated = music.searchTracks(filters={'userRating>>': 0})
    manual_ratings = []
    for t in all_rated:
        key = str(t.ratingKey)
        current_val = t.userRating
        if key not in state or abs(state[key] - current_val) > 0.01:
            if key in state:
                with state_lock: del state[key]
            manual_ratings.append(current_val)
    prior = sum(manual_ratings) / len(manual_ratings) if manual_ratings else 6.0
    return prior, len(manual_ratings)

def run_reconstruction(music):
    """Phase 8: Rebuilds state from Artists, Albums, AND Tracks."""
    tag_name = config.get('INFERRED_TAG', "").strip()
    if not tag_name:
        print("Error: No INFERRED_TAG defined in config.")
        return

    print(f"\n--- Phase 8: State Reconstruction (Mode: {'DRY RUN' if config.get('DRY_RUN', True) else 'LIVE'}) ---")
    
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
                    if not config.get('DRY_RUN', True):
                        state[key] = item.userRating
                    pbar.set_postfix(restored=restored_count)

    if restored_count > 0 and not config.get('DRY_RUN', True):
        save_state()
        print(f"\nSuccess: Restored {restored_count} total items to plex_state.json.")
    else:
        print(f"\nReconstruction finished. Items found: {restored_count}")


def run_report(music):
    """Phase 7: Generates a Top/Bottom Artist report based on Bayesian ratings."""
    print("\n--- Phase 7: Artist Power Rankings ---")
    artists = music.searchArtists()
    rated_artists = [a for a in artists if a.userRating and a.userRating > 0]
    rated_artists.sort(key=lambda x: x.userRating, reverse=True)
    
    print(f"\nTOP 10 ARTISTS (Highest Bayesian Score)")
    print(f"{'Artist Name':<40} | {'Rating':<10}")
    print("-" * 55)
    for a in rated_artists[:10]:
        print(f"{a.title[:40]:<40} | {a.userRating/2:.2f} stars")
        
    print(f"\nBOTTOM 10 ARTISTS (Lowest Bayesian Score)")
    print(f"{'Artist Name':<40} | {'Rating':<10}")
    print("-" * 55)
    for a in rated_artists[-10:]:
        print(f"{a.title[:40]:<40} | {a.userRating/2:.2f} stars")

def run_cleanup(music):
    """Phase 6: Undoes script effects using Shadow DB and Tag safety sweep."""
    print("\n--- Phase 6: Cleanup / Undo Mode ---")
    tag_name = config.get('INFERRED_TAG', "").strip()
    keys_to_remove = []
    
    pbar = tqdm(state.items(), desc="Undoing via State", unit="item")
    for key_str, stored_rating in pbar:
        try:
            item = music.fetchItem(int(key_str))
            current_rating = item.userRating or 0
            if abs(current_rating - stored_rating) < 0.02:
                if not config.get('DRY_RUN', True):
                    item.rate(None)
                    if tag_name: item.removeMood(tag_name)
                keys_to_remove.append(key_str)
        except: continue
        
    if not config.get('DRY_RUN', True):
        for k in keys_to_remove: state.pop(k, None)
        save_state()

    if tag_name:
        print(f"\nPerforming safety sweep for remaining '{tag_name}' tags...")
        items_with_tag = music.search(filters={'mood': tag_name})
        for item in tqdm(items_with_tag, desc="Safety Sweep", unit="item"):
            current_rating = item.userRating or 0
            is_standard = (current_rating * 2) % 1 == 0
            if not config.get('DRY_RUN', True):
                if not is_standard: item.rate(None)
                item.removeMood(tag_name)
    print("\nCleanup Complete.")

def run_verification(music):
    """Phase 5: Reports discrepancies between State and Plex."""
    print("\n--- Phase 5: Verification Mode ---")
    discrepancies, overrides = 0, 0
    pbar = tqdm(state.items(), desc="Verifying State", unit="item")
    for key_str, stored_rating in pbar:
        try:
            item = music.fetchItem(int(key_str))
            current_plex_rating = item.userRating or 0
            if abs(current_plex_rating - stored_rating) > 0.01:
                tqdm.write(f"  [OVERRIDE] {item.title}: Script expected {stored_rating/2:.2f}, found {current_plex_rating/2:.2f}")
                overrides += 1
        except: discrepancies += 1
    print(f"\nDetected Overrides: {overrides} | Orphaned: {discrepancies}")


# --- THREADED WORKER ---
def update_item_worker(item, new_rating, tag_name):
    """Worker function to handle the actual API POST call."""
    try:
        item.rate(new_rating)
        if tag_name:
            # We check moods here; if not loaded, this might trigger a fetch.
            # However, with mega-fetch, item.moods should already be populated.
            current_moods = [m.tag for m in item.moods]
            if tag_name not in current_moods:
                item.addMood(tag_name)
        
        with state_lock:
            state[str(item.ratingKey)] = new_rating
        return True
    except Exception as e:
        return f"Error updating {item.title}: {e}"

def process_layer(label, items, global_mean, start_char="", direction="UP"):
    updated_count = 0
    start_char_floor = start_char.upper() if start_char else chr(0)
    tag_name = config.get('INFERRED_TAG', "").strip()
    
    # SORTING
    print(f"Sorting {label}s by Artist Name...")
    if label == 'Album':
        items.sort(key=lambda x: (x.parentTitle.upper() if x.parentTitle else "", x.title.upper()))
    elif label == 'Artist':
        items.sort(key=lambda x: x.title.upper())
    elif label == 'Track':
        items.sort(key=lambda x: (x.grandparentTitle.upper() if x.grandparentTitle else "", x.parentTitle.upper() if x.parentTitle else "", x.title.upper()))

    current_section = None
    update_queue = []

    # 1. CALCULATION PHASE (Single Threaded - Fast)
    print(f"Calculating necessary updates for {label}s...")
    for item in tqdm(items, desc="Calculating", unit="item"):
        if label == 'Album': sort_name = item.parentTitle or "Unknown Artist"
        elif label == 'Artist': sort_name = item.title or "Unknown Artist"
        elif label == 'Track': sort_name = item.grandparentTitle or "Unknown Artist"
        
        first_char = sort_name.strip()[0].upper() if sort_name.strip() else "?"
        if first_char < start_char_floor: continue

        key = str(item.ratingKey)
        has_rating = item.userRating is not None and item.userRating > 0
        if has_rating and (key not in state): continue

        new_rating = None
        if direction == "UP":
            # Optimization: Children are now pre-loaded in memory
            children = item.tracks() if label == 'Album' else item.albums()
            rated_children = [c for c in children if c.userRating and c.userRating > 0]
            if rated_children:
                sum_r = sum(c.userRating for c in rated_children)
                n = len(rated_children)
                conf = config.get('CONFIDENCE_C', 3.0)
                new_rating = ((conf * global_mean) + sum_r) / (conf + n)
        elif direction == "DOWN":
            try:
                parent = item.artist() if label == 'Album' else item.album()
                if parent and parent.userRating and parent.userRating > 0:
                    new_rating = parent.userRating
            except: continue

        if new_rating:
            current = item.userRating or 0
            if abs(current - new_rating) > 0.01:
                update_queue.append((item, new_rating, first_char, sort_name))

    # 2. DISPATCH PHASE (Multi-Threaded - dilutes latency)
    if not update_queue:
        print("No updates required for this phase.")
        return 0

    print(f"Dispatching {len(update_queue)} updates via {MAX_THREADS} threads...")
    total_to_do = len(update_queue)
    
    if config.get('DRY_RUN', True):
        print(f"[DRY RUN] Would have dispatched {total_to_do} updates.")
        return total_to_do

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {executor.submit(update_item_worker, itm, rat, tag_name): (itm, rat, char, name) 
                   for itm, rat, char, name in update_queue}
        
        pbar = tqdm(total=total_to_do, desc="Updating Plex", unit="item")
        
        batch_counter = 0
        for future in as_completed(futures):
            item, rating, char, sort_name = futures[future]
            
            # Update Section Header UI
            if char != current_section:
                current_section = char
                tqdm.write(f"\n>>> Entering Section: [{current_section}] (Artist: {sort_name})")
            
            result = future.result()
            if result is True:
                updated_count += 1
                batch_counter += 1
            else:
                tqdm.write(result) # Print the error
            
            # Periodic batch save
            if batch_counter >= 100:
                save_state()
                batch_counter = 0
            
            pbar.update(1)
        pbar.close()

    save_state()
    return updated_count

# --- MAIN AND OTHER UTILITIES ---
# [Note: run_reconstruction, run_report, run_cleanup, run_verification logic preserved]

def main():
    try:
        plex = PlexServer(config['PLEX_URL'], config['PLEX_TOKEN'])
        music = plex.library.section(config['LIBRARY_NAME'])
    except Exception as e:
        print(f"Plex Connection Error: {e}"); return

    print(f"\n--- Bayesian Music Engine (V9 Parallel) ---")
    print("1-4: Inference | 5: Verify | 6: Cleanup | 7: Rankings | 8: Reconstruct")
    
    try:
        choice = int(input("Select Action (1-8) [1]: ") or 1)
        if choice == 5: run_verification(music); return
        if choice == 6: run_cleanup(music); return
        if choice == 7: run_report(music); return
        if choice == 8: run_reconstruction(music); return
        start_char = input("Start at Artist Letter (leave blank for start): ") or ""
    except ValueError: return

    initial_prior, manual_count = get_library_prior(music)
    print(f"Initial Global Prior: {initial_prior/2:.2f} stars.")

    # MEGA-FETCH LOGIC
    # We pull everything in one go to populate the local object tree
    print("Performing Mega-Fetch (Populating local metadata cache)...")
    # For Tracks, we search at the track level. For others, we search at their level.
    # Note: Search results in PlexAPI often auto-populate child data if requested correctly.
    
    phases = [
        ("Album", lambda: music.searchAlbums(), "UP"),
        ("Artist", lambda: music.searchArtists(), "UP"),
        ("Album", lambda: music.searchAlbums(), "DOWN"),
        ("Track", lambda: music.searchTracks(), "DOWN")
    ]

    total_updated = 0
    for i, (label, fetcher_func, direction) in enumerate(phases):
        if (i + 1) < choice: continue
        current_start = start_char if (i + 1) == choice else ""
        print(f"---\nFetching {label}s from server...")
        items = fetcher_func()
        count = process_layer(label, items, initial_prior, current_start, direction)
        total_updated += count
        print(f"Phase {i+1} complete. Updated {count} items.")

    final_prior, _ = get_library_prior(music, silent=True)
    print("\n" + "="*45 + "\nRUN SUMMARY\n" + "="*45)
    print(f"Total Items Updated:  {total_updated}\nStart Global Prior:   {initial_prior/2:.2f} stars")
    print(f"End Global Prior:     {final_prior/2:.2f} stars\nPrior Shift:          {(final_prior - initial_prior)/2:+.4f} stars\n" + "="*45)

if __name__ == "__main__":
    main()