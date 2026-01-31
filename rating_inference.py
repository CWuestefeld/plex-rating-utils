# -----------------------------------------------------------------------------
# Copyright (c) 2026 Chris Wuestefeld
# Licensed under the MIT License. See LICENSE in the project root for details.
# -----------------------------------------------------------------------------

import json
import os
import sys
import math
import time
from plexapi.server import PlexServer
from tqdm import tqdm

# --- Config & State loading ---
APP_VERSION = "1.3.1"
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
                "BULK_ALBUM_FILENAME": "./album_ratings.csv"
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

    data = load_json(STATE_FILE, {})
    
    # Detect format: New (dict with 'ratings') vs Old (flat dict)
    if 'ratings' in data:
        loaded_uuid = data.get('library_uuid')
        loaded_version = data.get('version')
        
        if loaded_version != APP_VERSION:
            print(f"Note: State file version ({loaded_version}) differs from program version ({APP_VERSION}).")
            
        if loaded_uuid and loaded_uuid != library.uuid:
            print(f"\nCRITICAL WARNING: State file UUID ({loaded_uuid}) does not match current library UUID ({library.uuid}).")
            print(f"Target Library: {library.title} ({library.uuid})")
            print(f"Are you using the wrong library?")
            confirm = input("Continuing may lead to incorrect ratings. Proceed? (y/n): ").lower()
            if confirm != 'y': sys.exit(1)
            
        state.update(data.get('ratings', {}))
    else:
        print("Note: Legacy state file format detected. Will upgrade on next save.")
        state.update(data)

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
                    if not config.get('DRY_RUN', True):
                        state[key] = item.userRating
                    pbar.set_postfix(restored=restored_count)

    if restored_count > 0 and not config.get('DRY_RUN', True):
        save_state()
        print(f"\nSuccess: Restored {restored_count} total items to plex_state.json.")
    else:
        print(f"\nReconstruction finished. Items found: {restored_count}")

def run_report(music):
    """Option 7: Generates a Top/Bottom Artist report based on Bayesian ratings."""
    print("\n--- Option 7: Artist Power Rankings ---")
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
                current_rating = item.userRating or 0
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
            if abs(current_plex_rating - stored_rating) > 0.01:
                tqdm.write(f"  [OVERRIDE] {item.title}: Script expected {stored_rating/2:.2f}, found {current_plex_rating/2:.2f}")
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
                # 1. Gather all children
                children = item.tracks() if label == 'Album' else item.albums()
                
                # 2. FILTER: Only use children NOT in our managed state (Manual ratings)
                # This prevents the "feedback loop" where inferred ratings inform parents
                manual_children = [c for c in children if (c.userRating or 0) > 0 and str(c.ratingKey) not in state]
                sum_manual = sum(c.userRating for c in manual_children)
                n_manual = len(manual_children)
                
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
                            inferred_rating = (parent_rating * (1 - gravity)) + (global_mean * gravity)
                        else:
                            inferred_rating = parent_rating
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
        else:
            print("Invalid option.")

def handle_bulk_actions_menu(music):
    """Displays and handles the Bulk Actions sub-menu."""
    while True:
        print("\n--- Bulk Actions ---")
        print(" 1: Power Rankings")
        print(" --------------------")
        choice = input("Select Option or <Enter> to return: ").strip().lower()

        if choice == '':
            return
        elif choice == '1':
            run_report(music)
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
        elif choice == 5: run_verification(music)
        elif choice == 6: run_cleanup(music)
        elif choice == 7: run_report(music)
        elif choice == 8: run_reconstruction(music)
        else:
            print(f"Invalid automation option: {choice}")
        return

    # --- INTERACTIVE MODE ---
    while True:
        print(f"\n======= Bayesian Music Rating Engine (v{APP_VERSION}) =======")
        print(   "-------  Copyright (c) 2026 Chris Wuestefeld  -------\n")
        print(" 0: FULL SEQUENCE (Runs Options 1-4)")
        print(" ----------------------------------------")
        print(" 1: Album-Up   (Track Ratings -> Albums)")
        print(" 2: Artist-Up  (Album Ratings -> Artists)")
        print(" 3: Album-Down (Artist Ratings -> Albums)")
        print(" 4: Track-Down (Album Ratings -> Tracks)")
        print(" ----------------------------------------")
        print(" A: Admin Tools")
        print(" B: Bulk Actions")
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

        try:
            # Default to 0 (FULL SEQUENCE) if user just hits enter
            choice = int(choice_str or 0)
            if not (0 <= choice <= 4):
                print("Invalid choice. Please select from 0-4, A, B, or X.")
                continue
        except ValueError:
            print("Invalid choice. Please select from 0-4, A, B, or X.")
            continue
        
        start_char = input("Start Artist Letter (Empty for ALL): ") or ""
        run_processing_phases(music, choice, start_char)

if __name__ == "__main__":
    main()