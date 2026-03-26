# State Registry Semantics

In `plex_state.json`, each rated item contains a dictionary. The primary structure is:

* **"r" (float)**: The actual rating value (0.0 to 10.0 scale) that the script assigned or identified.
* **"t" (int)**: This value signifies whether the track is part of a twin cluster.
  * **0**: Non-twin. This is the default value for a standard inferred rating. It means the rating was calculated through the normal upward (tracks to albums, albums to artists) or downward (artists to albums, albums to tracks) inference process.
  * **1**: Twin cluster member. This is set exclusively by the "Twin Logic" process. It signifies that the track is part of a twin cluster. It is currently the only non-zero value.
* **"m" (bool)**: Indicates if the rating originated from manual user input.
  * **false**: The rating was inferred by the script (either as a standard upward/downward inference or an inferred twin consensus).
  * **true**: The rating was manually set by the user. If `t` is 1, this means this specific track acted as a manual anchor for its twin cluster.

## Migration Note
In older versions, `t` had a value of `2` to indicate a twin manual anchor. This has been replaced by the `m` boolean flag, and `t` is now `1` for all members of a twin cluster. An automatic migration upgrades the old `t: 2` to `t: 1, m: true` upon load.