# Plex Bayesian Music Inference Engine

A Python-based utility for Plex Media Server that uses Bayesian math hierarchically to intelligently propagate ratings across your music library.

## The Motivation
Plex is great, and PlexAmp makes it even better. Especially with large music libraries, the Guest DJ and smart playlists make it easy to (re)discover your music. But these features are hobbled by the rating system. You have to individually rate each track for the app to be able to do anything with it.

For example, I like to start my morning listening to smooth jazz, so I've got a smart playlist that gathers tracks with that style. But I'd like to have it keep out tracks that I don't like (I hate vocal jazz; hopefully my programming is better than my taste), so I downvote those. But I can't put a rule into my smart playlist to only include tracks with a rating > 3, because there are a lot of tracks that I just haven't rated at all yet. Similarly, although Plex doesn't document this, the common wisdom is that the auto DJ features will stay away from tracks rated less < 2.5, but this is similarly limited by sparse rating data.

What if we could take what ratings you've got in your library, and generalize them across related tracks. Specifically, if we've rated a few tracks on an album highly, then we should be able to call that album a "good" album; and on a good album, it's likely that any unrated tracks are good.

## The Concept
Standard Plex ratings are "flat". Rating a track doesn't influence the album, and rating an album doesn't influence the artist, so the ratings wind up being very sparse. This tool solves that problem with:
1. **Bottom-Up Inference:** Calculating Album and Artist ratings based on a Bayesian average of their children.
2. **Top-Down Inheritance:** Allowing unrated Tracks to inherit ratings from their parent Albums.
3. **Precision:** Utilizing high-precision floating-point ratings (e.g., 3.74 stars) to power more granular smart playlists and Auto-DJ features.

While doing this, it tracks which ratings it has inferred so that your own ratings will always be the data driving the calculations, and avoids turning the rating process into a feedback loop.

## Why Bayesian?
The rationale behind assuming that an unrated track should get the same rating (if known) as the album it appears on should be pretty obvious. But what about that business about using "Bayesian averages"? Well, imagine that you've got an album, and only one of its tracks is rated, with 5 stars. If we just set an album naively to the average of its tracks, then we'd set our hypothetical album to 5 overall. But that's almost certainly not true: I'll bet that you've got far fewer "5" albums in your library than you have individual "5" tracks. A better solution is to start from the assumption that any unrated track on the album is just the average across all tracks in your library, and let the known tracks on the album demonstrate that they should be better. So we start with a guess (this is the "Global Prior" you'll see output during processing) and let good and bad tracks on the album pull that average up or down.

## Key Features
- **Restartable:** Massive libraries are handled via a phased, checkpoint-based approach. If something happens forcing it to stop partway through, you can restart with minimal wasted work.
- **Bayesian Priors:** Uses a "Confidence Constant" to ensure that a single 5-star track (or 1-star, for that matter) doesn't unfairly dictate an entire album's score.
- **Non-Destructive:** Includes a full Cleanup/Undo mode to revert all script-applied ratings.
- **Shadow DB (Safe):** Uses a local file, `plex_state.json`, to distinguish between script-generated ratings and your manual ratings. It will never overwrite your manual work.
- **Tagging inferred data:**  Optionally add a `mood` tag to each track/album/artist so you can see which ratings are inferred.
- **Reporting:** Get a report of how many items were updated, how the global prior was shifted by the run, and a list of your top-rated and bottom-rated artists.

## Setup
1. Clone this repository.
2. Install dependencies: `pip install plexapi tqdm`
3. Create a `config.json` based on the provided template:
```
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
```

## Usage
Run the script: `python rating_inference.py`

Select from the phases:
- **Option 0:** Run through the whole cycle, 1-4, automatically.
- **Options 1-2 (Up):** Generate Album/Artist ratings from Tracks.
- **Options 3-4 (Down):** Push ratings to unrated items.
- **Option 5:** Verify state synchronization. Determine whether you need to re-run the utility.
- **Option 6:** Full Cleanup/Undo.
- **Option 7:** Power Rankings (Top/Bottom Artists).
- **Option 8:** Recover internal data. If you deleted the plex_state.json file, attempt to rebuild from the Plex database (requires that `INFERRED_TAG` had been used).

If you've got a reasonably-sized library, you can just choose 0 and let the whole thing run. But if you've got a large library, you'll probably want to do it in phases.

If you choose 1-4, you'll also get prompted for where to start. If you just hit enter to accept the default, you'll start at the beginning. But if you needed to interrupt the process midstream, this will allow you to skip the part you already completed, restarting from the letter it was processing when stopped. In other words, if you stopped processing in the middle of the letter "P", then you can pick up where you left off by entering "P" at this prompt, and it'll skip past everything up through "O", and just start on the P's.

If you want to automate running the tool, you can run it by including on the command line which Option you want to run. For example, 
`python rating_inference.py 4`
This will behave exactly as if you'd run it interactively, requesting "4" at the prompt and then accepting the default of starting at the beginning of the library.

The `config.json` setting `"INFERRED_TAG": "Rating_Inferred"` tells the utility to put in the `mood` tag to mark ratings that were inferred. If you don't want to clutter those tags, set this to an empty string: `"INFERRED_TAG": ""` and we won't tag it. We'll track which items we updated in the `plex_state.json` file. **DO NOT** delete that file!

**Please** take advantage of the DRY_RUN feature to sanity check before running it for real. I don't want to feel bad because your library got mangled. Look in the `config.json` described above: if the setting for `DRY_RUN` is `true`, then it won't actually save the results back into Plex. When you're ready, change this setting to `false` to let the utility actually write the changes.

## Caveats
OK, enough with trying to sell you on using this. Why might you *not* want to use it?

Running this needs to move a lot of data to perform its calculations. Plex's internal database management is single-threaded, and its performance seems to get very noticeably worse for larger libraries. For really large libraries, a full initial run can take days. Even a moderately-sized library might take a couple hours. Subsequent runs should be much faster, especially if you leave **dynamic precision** enabled so it won't need to update until the values drift farther apart.

If you decide later that you don't like what this did, and want to undo all the changes, you can use Option 6 for **Cleanup**. But note that it's going to be just as slow as the initial calculation run. That's because the real bottleneck here isn't the computation, but getting Plex to store all the changes. Whether we're setting a rating or deleting a rating isn't going to make a ton of difference in performance.

If the performance is a real problem for you, you can improve it significantly by disabling the tagging of inferred values. Just go to your `config.json` file and change `"INFERRED_TAG": "Rating_Inferred"` to `"INFERRED_TAG": ""`. That eliminates much of the data that Plex has to write, significantly mitigating the bottleneck. In my testing, this saves about 40% of processing time. Just remember that by doing this, you're effectively disabling the **Option 8: Recovery** feature.

If you need to pause or cancel processing, hit Control-C as if you were trying to kill the program. You'll get a prompt whether you want to Resume or Quit. Entering Q will just quit. But if you want to continue on, enter R. This is useful if you need to pause for some reason. 

Don't delete the `plex_state.json` file. We need that to track whether you've made manual updates to the Ratings. If you do delete it (and if `INFERRED_TAG` had been used originally) we can rebuild it, but we'll miss any manual changes you might have made since the last run.

The tool is designed only to handle a **single music library**. If you've got more than one, and you want to use this tool on them, you'll need to juggle the `plex_state.json` file together with the value of `LIBRARY_NAME`. Make sure that you always have the correct `plex_state.json` in place for the library you're running against.

## Hints and Tips

I've found it useful on the first pass through a really big library to pause processing temporarily to re-optimize your Plex database. Hit Control-C to get the menu. Without responding to the prompt yet, go over to Plex and in the server's Troubleshooting section, push the **Optimize Database** button. When that completes, go back to the script and reply R to resume.

Here are some real-world performance measurements, taken for a 7,500 track library.

| operation | WITH tags | NO tags |
| --------- | --------- | ------- |
| full RUN  |     17:30 |   11:00 |
| full UNDO |     16:00 |    8:00 |

But as library size increases, the scaling is *greater than* linear.

### The Initial "Baseline" Run

1. **Manual Rating Audit:** Before running the script for the first time, ensure your manual ratings are exactly where you want them. The script treats anything already rated (and not in the state file) as a "Manual Hijack" and will never touch it.
2. **Options 1 & 2 (The "Up" Pass):** Always run these first. This establishes your Artist and Album "Power Scores" based on your track-level taste.
3. **Options 3 & 4 (The "Down" Pass):** Run these only after you are satisfied with your Artist/Album scores. Option 4 (Track-Down) is the most intensive and should ideally be run overnight.

### The "Maintenance" Cycle

You don't need to run a full 4-phase inference every day.

* **Weekly:** Run Phase 1 & 2 to incorporate any new tracks you've rated.
* **Monthly:** Run the full 1-4 cycle to let the "Drift" logic catch up with the evolving Global Prior.
* **After Re-organizing:** Run Phase 5 (Verify) to ensure your local `plex_state.json` still matches the server IDs.

### Understanding the "Drift" Logic

The engine uses a **Dynamic Precision** threshold based on library size.

* For a library of 50,000 tracks, the tool will accept drift up to about 0.13 stars (i.e., 1/8 star). 300,000 items would allow 0.17 stars.
* If the Bayesian math suggests a track should be 3.84 stars, but it is currently 3.75, the script will **skip the update** to save your CPU and disk I/O - and more importantly, your time.
* If you're a real stickler and want absolute precision, you can update your config.json file, setting `"DYNAMIC_PRECISION": false`. That'll disable the tolerance, forcing updates.

To avoid completely swamping your Plex server, we take a brief break occasionally. This is controlled by the `config.json` settings `COOLDOWN_BATCH` and `COOLDOWN_SLEEP`. The default settings are to break every 25 items, taking a 5 second pause. Feel free to adjust those as you'd like.