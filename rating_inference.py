import json
import os
import sys
import math
from plexapi.server import PlexServer
from tqdm import tqdm

# --- CONFIG & STATE LOADING ---
CONFIG_FILE = 'config.json'
STATE_FILE = 'plex_state.json'

def load_json(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

config = load_json(CONFIG_FILE, {})
state = load_json(STATE_FILE, {})

def save_state():
    """Saves the current inference registry to disk (Thread Safe)."""
    if config.get('DRY_RUN', True): return 
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)

def calculate_dynamic_epsilon(item_count):
    """
    Scales the 'Close Enough' threshold. 
    Small libs: ~0.02 | 300k lib: ~0.15
    """
    if not config.get('DYNAMIC_PRECISION', True): return 0.02
    if item_count < 1000: return 0.02
    return round(0.05 * (math.log10(item_count)-2), 3)

def get_library_prior(music, silent=False):
    """Calculates the Bayesian Prior using only Manual (User) ratings."""
    if not silent: print("Calculating Global Prior (Manual ratings only)...")
    all_rated = music.searchTracks(filters={'userRating>>': 0})
    manual_ratings = []
    for t in all_rated:
        key = str(t.ratingKey)
        current_val = t.userRating
        if key not in state or abs(state[key] - current_val) > 0.01:
            if key in state: del state[key]
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

def process_layer(label, items, global_mean, start_char="", direction="UP"):
    updated_count, skipped_count, hijacked_count = 0, 0, 0
    start_char_floor = start_char.upper() if start_char else chr(0)
    tag_name = config.get('INFERRED_TAG', "").strip()

    # Validation of the math constant
    c_val = config.get('CONFIDENCE_C', 3.0)
    if c_val <= 0:
        print("Error: CONFIDENCE_C must be a positive number. Defaulting to 3.0.")
        c_val = 3.0
    
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
        # Section Header Logic
        if label == 'Album': sort_name = item.parentTitle or "Unknown"
        elif label == 'Artist': sort_name = item.title or "Unknown"
        elif label == 'Track': sort_name = item.grandparentTitle or "Unknown"
        
        first_char = sort_name.strip()[0].upper() if sort_name.strip() else "?"
        if first_char < start_char_floor: continue
        if first_char != current_section:
            current_section = first_char
            tqdm.write(f"\n>>> Section: [{current_section}] ({sort_name})")

        key = str(item.ratingKey)
        plex_rating = item.userRating or 0.0
        state_rating = state.get(key) # None if Case A/B, float if C/D/E

        # --- CASE C: MANUAL HIJACK DETECTION ---
        # We thought we owned it, but the Plex value changed since our last write.
        if state_rating is not None and abs(state_rating - plex_rating) > 0.01:
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
            children = item.tracks() if label == 'Album' else item.albums()
            rated_children = [c for c in children if c.userRating and c.userRating > 0]
            if rated_children:
                sum_r = sum(c.userRating for c in rated_children)
                n = len(rated_children)
                inferred_rating = ((c_val * global_mean) + sum_r) / (c_val + n)
        elif direction == "DOWN":
            try:
                parent = item.artist() if label == 'Album' else item.album()
                if parent and parent.userRating and parent.userRating > 0:
                    inferred_rating = parent.userRating
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
                    state[key] = inferred_rating
                    if tag_name and tag_name not in [m.tag for m in item.moods]:
                        item.addMood(tag_name)
                updated_count += 1
        
        # Periodic Save
        if updated_count % 100 == 0 and updated_count > 0:
            save_state()

    if not config.get('DRY_RUN', True): save_state()
    print(f"Complete: {updated_count} Updated, {skipped_count} Drift-Skipped, {hijacked_count} Hijacks Resolved.")
    return updated_count

def main():
    try:
        plex = PlexServer(config['PLEX_URL'], config['PLEX_TOKEN'])
        music = plex.library.section(config['LIBRARY_NAME'])
    except Exception as e:
        print(f"Plex Connection Error: {e}"); return

    print(f"\n======= Bayesian Music Engine (V12) =======\n")
    print(" 0: FULL SEQUENCE (Runs Options 1-4)")
    print(" ----------------------------------------")
    print(" 1: Album-Up   (Track Ratings -> Albums)")
    print(" 2: Artist-Up  (Album Ratings -> Artists)")
    print(" 3: Album-Down (Artist Ratings -> Albums)")
    print(" 4: Track-Down (Album Ratings -> Tracks)")
    print(" ----------------------------------------")
    print(" 5: Verify State   | 6: Cleanup/Undo")
    print(" 7: Power Rankings | 8: Reconstruct State")
    
    try:
        choice = int(input("\nSelect Option [0-8]: ") or 0)
    except ValueError: return

    # Handle Administrative Options (5-8)
    if choice == 5: run_verification(music); return
    if choice == 6: run_cleanup(music); return
    if choice == 7: run_report(music); return
    if choice == 8: run_reconstruction(music); return

    # Checkpoint/Start Letter
    start_char = input("Start Artist Letter (Empty for ALL): ") or ""

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
        print("Invalid choice.")
        return

    # EXECUTION LOOP
    for i, (label, fetch_func, direction) in enumerate(workload):
        # We only apply the start_char to the FIRST phase of whatever the workload is
        current_start = start_char if i == 0 else ""
        
        print(f"\n>>> Executing Option {choice if choice != 0 else i+1}: {label}-{direction}")
        print(f"Fetching {label}s")
        items = fetch_func()
        process_layer(label, items, prior, current_start, direction)

    final_prior, _ = get_library_prior(music, silent=True)
    print("\n" + "="*45 + "\nRUN SUMMARY\n" + "="*45)
    print(f"Total Items Updated:  {total_updated}\nStart Global Prior:   {initial_prior/2:.2f} stars")
    print(f"End Global Prior:     {final_prior/2:.2f} stars\nPrior Shift:          {(final_prior - initial_prior)/2:+.4f} stars\n" + "="*45)

if __name__ == "__main__":
    main()