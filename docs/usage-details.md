## Usage Details

Run the script: `python rating_inference.py`

Select from the phases:

- **Option 0:** Run through the whole cycle, 1-5, automatically.

- **Options 1-2 (Up):** Generate Album/Artist ratings from Tracks.

- **Options 3-4 (Down):** Push inherited ratings to unrated child items.

- **Option 5:** Extrapolate ratings for twin Tracks to other copies of that same Track.

- **(A) Admin Tools:** Verify synchronization with Plex, Cleanup/Undo, Reconstruct State, Synchronize Plex Tags.

- **(B) Bulk Actions:** access the submenu to execute bulk actions (import/export).

- **(R) Reports:** access the submenu to run reports.

- **eXit:** Choose **X** to leave the app.

If you've got a reasonably-sized library, you can just choose 0 and let the whole thing run. But if you've got a large library, you'll probably want to do it in phases.

If you choose 1-4, you'll also get prompted for where to start. If you just hit enter to accept the default, you'll start at the beginning. But if you had previiously needed to interrupt the process midstream, this will allow you to skip the part you already completed, restarting from the letter it was processing when stopped. In other words, if you stopped processing in the middle of the letter "P", then you can pick up where you left off by entering "P" at this prompt, and it'll skip past everything up through "O", and just start on the P's.

If you want to automate running the tool, you can run it by including on the command line which Option you want to run. For example,

`python rating_inference.py 4`

This will behave exactly as if you'd run it interactively, requesting "4" at the prompt and then accepting the default of starting at the beginning of the library.

The `config.json` setting `"INFERRED_TAG": "Rating_Inferred"` tells the utility to put in the `mood` tag to mark ratings that were inferred. If you don't want to clutter those tags, set this to an empty string: `"INFERRED_TAG": ""` and we won't tag it. We'll track which items we updated in the `plex_state.json` file. **DO NOT** delete that file!

**Please** take advantage of the DRY_RUN feature to sanity check before running it for real. I don't want to feel bad because your library got mangled. Look in the `config.json` described above: if the setting for `DRY_RUN` is `true`, then it won't actually save the results back into Plex. When you're ready, change this setting to `false` to let the utility actually write the changes.

## Caveats

OK, enough with trying to sell you on using this. Why might you *not* want to use it?

Running this needs to move a lot of data to perform its calculations. Plex's internal database management is single-threaded, and its performance seems to get very noticeably worse for larger libraries. For really large libraries, a full initial run can take days if you use an `INFERRED_TAG`. Even a moderately-sized library might take a couple hours. Subsequent runs should be much faster, especially if you leave **dynamic precision** enabled so it won't need to update until the values drift farther apart. If you've got a really big library, it's probably worth it to forego that tagging.

If you decide later that you don't like what this did, and want to undo all the changes, you can use Option 6 for **Cleanup**. But note that it's going to be just as slow as the initial calculation run. That's because the real bottleneck here isn't the computation, but getting Plex to store all the changes. Whether we're setting a rating or deleting a rating isn't going to make a ton of difference in performance.



If you need to pause or cancel processing, hit Control-C as if you were trying to kill the program. You'll get a prompt whether you want to Resume or Quit. Entering Q will just quit. But if you want to continue on, enter R. This is useful if you need to pause for some reason.

**Don't delete the `plex_state.json` file.** We need that to track whether you've made manual updates to the Ratings. If you do delete it (and if `INFERRED_TAG` had been used originally) we can rebuild it, but we'll miss any manual changes you might have made since the last run.

The tool is designed only to handle a **single music library**. If you've got more than one, and you want to use this tool on them, you'll need to juggle the `plex_state.json` file together with the value of `LIBRARY_NAME`. Make sure that you always have the correct `plex_state.json` in place for the library you're running against. The tool will try to protect you from mistakes here. The state file is stamped with the internal UUID of the Plex library that it was generated from. If you run it pointing at a library that doesn't match this stamp, you'll get a stern warning, but it'll let you proceed if you really want to.

## Hints and Tips

I've found it useful on the first pass through a really big library to pause processing temporarily to re-optimize your Plex database. Hit Control-C to get the menu. Without responding to the prompt yet, go over to Plex and in the server's Troubleshooting section, push the **Optimize Database** button. When that completes, go back to the script and reply R to resume.

Here are some real-world performance measurements, taken for a 7,500 track library, **while using the `INFERRED_TAG` feature**.

| operation | WITH tags (mm:ss) | NO tags (mm:ss) |
| --------- | ----------------- | --------------- |
| full RUN  | 17:30             | 11:00           |
| full UNDO | 16:00             | 8:00            |

But as library size increases, the scaling is *greater than* linear. That is, a library twice as big will take *more than* twice as long to process.

If the performance is a real problem for you, you can improve it significantly by disabling the tagging of inferred values. Just go to your `config.json` file and change `"INFERRED_TAG": "Rating_Inferred"` to `"INFERRED_TAG": ""`. That eliminates much of the data that Plex has to write, significantly mitigating the bottleneck. In my testing, this saves about 40% of processing time. Just remember that by doing this, you're effectively disabling the **Option 8: Recovery** feature.

To avoid completely swamping your Plex server, we take a brief break occasionally. This is controlled by the `config.json` settings `COOLDOWN_BATCH` and `COOLDOWN_SLEEP`. The default settings are to break every 25 items, taking a 5 second pause. Feel free to adjust those as you'd like.
