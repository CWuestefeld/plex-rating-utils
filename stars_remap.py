from plexapi.server import PlexServer

# --- CONFIGURATION ---
PLEX_URL = 'http://crimson:32400' # Change to your server IP
PLEX_TOKEN = 'hxrsSoxsdpLFzPQmH3LC'
LIBRARY_NAME = "'Music"
DRY_RUN = False  # Set to False to actually apply changes

# Mapping: Current Internal Rating (0-10) -> New Internal Rating (0-10)
# 1.0 (2) & 1.5 (3) -> 1 (2)
# 2.0 (4) & 2.5 (5) -> 2 (4)
# 3.0 (6)           -> 3 (6)
# 3.5 (7) & 4.0 (8) -> 4 (8)
# 4.5 (9) & 5.0 (10)-> 5 (10)
RATING_MAP = {
    2.0: 2.0, 3.0: 2.0,  # 1.0, 1.5 -> 1.0
    4.0: 4.0, 5.0: 4.0,  # 2.0, 2.5 -> 2.0
    6.0: 6.0,            # 3.0      -> 3.0
    7.0: 8.0, 8.0: 8.0,  # 3.5, 4.0 -> 4.0
    9.0: 10.0, 10.0: 10.0 # 4.5, 5.0 -> 5.0
}

def update_ratings():
    try:
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        music = plex.library.section(LIBRARY_NAME)
    except Exception as e:
        print(f"Error connecting to Plex: {e}")
        return

    # Statistics Tracker
    stats = {
        'Artist': {'found': 0, 'updated': 0},
        'Album':  {'found': 0, 'updated': 0},
        'Track':  {'found': 0, 'updated': 0}
    }

    types = [('Artist', music.searchArtists), 
             ('Album', music.searchAlbums), 
             ('Track', music.searchTracks)]

    print(f"Connected to {plex.friendlyName}. Starting processing...")

    for label, search_func in types:
        print(f"\nScanning {label}s...")
        # Get only items that have a user rating already set
        items = search_func(filters={'userRating>>': 0})
        stats[label]['found'] = len(items)
        
        for item in items:
            old_rating = item.userRating
            
            if old_rating in RATING_MAP:
                new_rating = RATING_MAP[old_rating]
                
                if old_rating != new_rating:
                    stats[label]['updated'] += 1
                    status = "[DRY RUN]" if DRY_RUN else "[UPDATING]"
                    print(f"  {status} {item.title}: {old_rating/2} -> {new_rating/2}")
                    
                    if not DRY_RUN:
                        try:
                            item.rate(new_rating)
                        except Exception as e:
                            print(f"    Error updating {item.title}: {e}")

    # --- Summary Report ---
    print("\n" + "="*30)
    print("FINAL SUMMARY REPORT")
    print("="*30)
    print(f"{'Object Type':<12} | {'Rated Found':<12} | {'To Update':<12}")
    print("-" * 42)
    
    for label in ['Artist', 'Album', 'Track']:
        found = stats[label]['found']
        updated = stats[label]['updated']
        print(f"{label:<12} | {found:<12} | {updated:<12}")
    
    print("="*30)
    if DRY_RUN:
        print("Note: This was a DRY RUN. No changes were committed to the database.")

if __name__ == "__main__":
    update_ratings()