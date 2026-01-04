# Plex Bayesian Music Inference Engine

A Python-based utility for Plex Media Server that uses Bayesian math hierarchically to intelligently propagate ratings across your music library.

## The Motivation
Plex is great, and PlexAmp makes it even better. Especially with large music libraries, the Guest DJ and smart playlists make it easy to (re)discover your music. But these features are hobbled by the rating system. You have to individually rate each track for the app to be able to do anything with it.

For example, I like to start my morning listening to smooth jazz, so I've got a smart playlist that gathers tracks with that style. But I'd like to have it keep out tracks that I don't like (I hate vocal jazz; hopefully my programming is better than my taste), so I downvote those. But I can't put a rule into my smart playlist to only include tracks with a rating > 3, because there are a lot of tracks that I just haven't rated at all yet. Similarly, although Plex doesn't document this, the common wisdom is that the auto DJ features will stay away from tracks rated less < 2.5, but this is similarly limited by sparse rating data.

What if we could take what ratings you've got in your library, and generalize them across related tracks. Specifically, if we've rated a few tracks on an album highly, then we should be able to call that album a "good" album; and on a good album, it's likely that any unrated tracks are good.

## The Concept
Standard Plex ratings are "flat"—rating a track doesn't influence the album, and rating an album doesn't influence the artist. This tool solves the "Sparsely Rated Library" problem by:
1. **Bottom-Up Inference:** Calculating Album and Artist ratings based on a Bayesian average of their children.
2. **Top-Down Inheritance:** Allowing unrated Tracks to inherit ratings from their parent Albums.
3. **Precision:** Utilizing high-precision floating-point ratings (e.g., 3.74 stars) to power more granular smart playlists and Auto-DJ features.

## Why Bayesian?
The rationale behind assuming that an unrated track should get the same rating (if known) as the album it appears on should be pretty obvious. But what about that business about using "Bayesian averages"? Well, imagine that you've got an album with just one track on it, rated 5. If we just set an album naively to the average of its tracks, then we'd set our hypothetical album to 5 overall. But that's almost certainly not true: I'll bet that you've got far fewer "5" albums in your library than you have individual "5" tracks. A better solution is to start from the assumption that any unrated track on the album is just the average across all tracks in your library, and let the known tracks on the album demonstrate that they should be better. So we start with a guess (this is the "Global Prior" you'll see output during processing) and let good and bad tracks on the album pull that average up or down.

## Key Features
- **Restartable:** Massive libraries are handled via a phased, checkpoint-based approach. If something happens forcing it to stop partway through, you can restart with minimal wasted work.
- **Bayesian Prior:** Uses a "Confidence Constant" to ensure that a single 5-star track (or 1-star, for that matter) doesn't unfairly inflate an entire album's score.
- **Non-Destructive:** Includes a full Cleanup/Undo mode to revert all script-applied ratings.
- **Shadow DB (Safe):** Uses a local `plex_state.json` to distinguish between script-generated ratings and your manual ratings. It will never overwrite your manual work.
- **Tagging inferred data:**  Optionally add a `mood` tag to each track/album/artist so you can see which ratings are inferred.
- **Reporting:** Get a report of how many items were updated, how the global prior was shifted by the run, and a list of your top-rated and bottom-rated artists.

## Setup
1. Clone this repository.
2. Install dependencies: `pip install plexapi tqdm`
3. Create a `config.json` based on the provided template:
   ```json
   {
     "PLEX_URL": "http://your-server-ip:32400",
     "PLEX_TOKEN": "your-token-here",
     "LIBRARY_NAME": "Music",
     "CONFIDENCE_C": 3.0,
     "DRY_RUN": true,
     "INFERRED_TAG": "Rating_Inferred",
     "DYNAMIC_PRECISION": true,
     "COOLDOWN_BATCH": 25,
     "COOLDOWN_SLEEP": 5
   }


## Usage
Run the script: `python rating_inference.py`

Select from the phases:
- **Options 1-2 (Up):** Generate Album/Artist ratings from Tracks.
- **Options 3-4 (Down):** Push ratings to unrated items.
- **Option 5:** Verify state synchronization. Determine whether you need to re-run the utility.
- **Option 6:** Full Cleanup/Undo.
- **Option 7:** Power Rankings (Top/Bottom Artists).
- **Option 8:** Recover internal data. If you deleted the plex_state.json file, attempt to rebuild from the Plex database (requires that `INFERRED_TAG` had been used).

If you choose 1-4, you'll be prompted for where to start. If you just accept the default by hitting enter, you'll start at the beginning. But if you needed to interrupt the process midstream, this will allow you to skip the part you already completed, restarting from the letter it was processing when stopped.

The `config.json` setting `"INFERRED_TAG": "Rating_Inferred"` tells the utility to put in the `mood` tag to mark ratings that were inferred. If you don't want to clutter those tags, set this to an empty string: `"INFERRED_TAG": ""` and we won't tag it. We'll track which items we updated in the `plex_state.json` file. **DO NOT** delete that file!

**Please** take advantage of the DRY_RUN feature to sanity check before running it for real. I don't want to feel bad because your library got mangled. Look in the `config.json` described above: if the setting for DRY_RUN is `true`, then it won't actually save the results back into Plex. When you're ready, change this setting to `false` to let the utility actually write the changes.

## Caveats
This needs to move a lot of data to perform its calculations. Plex's internal database management is single-threaded, and its performance seems to get very noticeably worse for larger libraries. For really large libraries, this can take many hours. Even a moderately-sized library might take a couple hours.

Don't delete the `plex_state.json` file. We need that to track whether you've made manual updates to the Ratings. If you do delete it (and if `INFERRED_TAG` had been used originally) we can rebuild it, but we'll miss any manual changes you might have made since the last run.

Because Plex doesn't track whether Ratings have changed, we need to infer it ourselves (for other fields, Plex puts a lock on changed fields so their Agent doesn't overwrite in the future, but this doesn't apply to Ratings). If the utility hasn't processed it before and there's a Rating, this is easy: it must be a manual entry that should be respected. But if the utility stuffs in a value that you later update with your real rating, it's not so clear. We determine that it was changed by comparing the current Rating to what inferred value we'd previously stuffed in. This will almost always be different, so any difference would prove that you've changed it yourself. That's because we're using real numbers internally, so if you see three stars in PlexAmp after we infer the value, that's probably something like 3.17 internally; if you click three stars yourself, the value will change to 3.0* so even if it looks the same to you, we should normally be able to tell the difference.

*actually, it wouldn't be 3.0, but 6.0. Even though Plex displays a 5-star scale, their internal representation is 1-10.

## Hints and Tips

### The Initial "Baseline" Run

1. **Manual Rating Audit:** Before running the script for the first time, ensure your manual ratings are exactly where you want them. The script treats anything already rated (and not in the state file) as a "Manual Hijack" and will never touch it.
2. **Options 1 & 2 (The "Up" Pass):** Always run these first. This establishes your Artist and Album "Power Scores" based on your track-level taste.
3. **Options 3 & 4 (The "Down" Pass):** Run these only after you are satisfied with your Artist/Album scores. Option 4 (Track-Down) is the most intensive and should ideally be run overnight.

### The "Maintenance" Cycle

You don't need to run a full 4-phase inference every day.

* **Weekly:** Run Phase 1 & 2 to incorporate any new tracks you've rated.
* **Monthly:** Run the full 1–4 cycle to let the "Drift" logic catch up with the evolving Global Prior.
* **After Re-organizing:** Run Phase 5 (Verify) to ensure your local `plex_state.json` still matches the server IDs.

### Understanding the "Drift" Logic

The engine uses a **Dynamic Epsilon** threshold based on library size.

* For a library of **300,000 items**, the script accepts a drift of roughly **~0.17 stars**.
* If the Bayesian math suggests a track should be 3.84 stars, but it is currently 3.75, the script will **skip the update** to save your CPU and disk I/O - and more importantly, your time.
