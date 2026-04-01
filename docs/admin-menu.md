# Admin Menu Features

The Admin Tools menu provides several utilities for managing the state and synchronization of your Plex music library with the Bayesian Music Rating Engine.

## Options:

### 1: Verify State
This option allows you to check for discrepancies between the script's internal `plex_state.json` file and the actual ratings in your Plex library. It reports:
- **Overrides:** Items where the rating in Plex differs from what the script expects based on its state file. This often indicates a manual rating change by the user.
- **Orphaned:** Items that are present in the script's state file but could not be found in the Plex library (e.g., deleted items).

### 2: Cleanup/Undo
This powerful feature allows you to revert all ratings applied by the script. It performs the following actions:
- Resets script-inferred ratings in Plex back to unrated.
- Removes the `INFERRED_TAG` (if configured) from items that were tagged by the script.
- Clears the corresponding entries from the `plex_state.json` file.
- Includes a "safety sweep" to ensure all `INFERRED_TAG` tags are removed from items that are no longer inferred by the script.

### 3: Reconstruct State
This option rebuilds the `plex_state.json` file based on existing Plex ratings and the `INFERRED_TAG`. It's useful if your `plex_state.json` file becomes corrupted or is lost. It will scan your library for items that have a rating and the `INFERRED_TAG`, and add them back to the state file as inferred ratings.

### 4: Synchronize Plex Tags
This utility scans all items in your library and ensures that the `INFERRED_TAG` (as defined in `config.json`) is correctly applied or removed based on whether the script considers the item's rating to be inferred or manual. This is useful for cleaning up tags if they get out of sync.