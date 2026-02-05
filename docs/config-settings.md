## Sample config.json

{
{
  "version": "1.4.0",
  "PLEX_URL": "http://192.168.0.123:32400",
  "PLEX_TOKEN": "abcdefg12345",
  "LIBRARY_NAME": "'Music",
  "CONFIDENCE_C": 3.0,
  "BIAS_CRITIC": 1.5,
  "WEIGHT_CRITIC": 3.0,
  "WEIGHT_GLOBAL": 1.0,
  "DRY_RUN": true,
  "INFERRED_TAG": "Rating_Inferred",
  "DYNAMIC_PRECISION": true,
  "COOLDOWN_BATCH": 50,
  "COOLDOWN_SLEEP": 5,
  "ALBUM_INHERITANCE_GRAVITY": 0.9,
  "TRACK_INHERITANCE_GRAVITY": 0.3,
  "BULK_ARTIST_FILENAME": "./artist_ratings.csv",
  "BULK_ALBUM_FILENAME": "./album_ratings.csv",
  "BULK_TRACK_FILENAME": "./track_ratings.csv",
  "TWIN_LOGIC": {
      "ENABLED": true,
      "DURATION_TOLERANCE_SEC": 5,
      "EXCLUDE_KEYWORDS": ["live", "demo", "reprise", "instrumental", "commentary", "acoustic", "remix"],
      "EXCLUDE_PARENTHESES": true,
      "EXCLUDE_LIVE_ALBUMS": true,
      "TWIN_TAG": "Twin"
  },
  "UPWARD_EXCLUSION_RULES": {
      "ENABLED": true,
      "MIN_DURATION_SEC": 80,
      "KEYWORDS": ["intro", "outro", "interview", "skit", "applause", "commentary"],
      "CASE_SENSITIVE": false
  }
} 
}

## Explanation

**version** : The version of the app that this file is intended for. This is a safety feature in case of future breaking changes, and possibly to help migrate for newer versions.

**PLEX_URL** : The URL of your Plex server. This normally specifies port 32400.

**PLEX_TOKEN** : This is like a password into your plex server. Read this article to learn how to find yours: [Finding an authentication token / X-Plex-Token | Plex Support](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)

**LIBRARY_NAME** : The name of the music library you want to process.

**CONFIDENCE_C** : This sets a weighting factor for how the global average is factored into the total rating of an album.

    It's basically like saying "include this number of virtual tracks with the global-average rating, when calculating the album's overall rating".

**BIAS_CRITIC** : This value is added to critic ratings (remember that this is on a 10-point scale), and then that value is normalized back into a 1-10 scale. This is provided because critics seem to use numbers that suggest "bad album" when what they really mean is "not great" or "not up to the artist's previous standards".

**WEIGHT_CRITIC** : How much to weight the critic's rating (if any) versus the global average. Since this implicitly contains more information (i.e., which specific album, rather than just a global value), it makes sense to weight it more heavily. Good values should range 1-5.

**WEIGHT_GLOBAL** : How much to weight the global average, relative the critic's rating above. I like 1.0, but do as you like.

**DRY_RUN** : Set to "true" to run this to see what would happen within making any real changes. Once you're happy with that, change the value to "false" and run it again. Please, do a dry run first.

**INFERRED_TAG** : The name of a tag that will be added to moods for each item that gets an inferred rating. This allows you to see in Plex which items got inferred ratings, and so if you want, you could exclude them from a playlist or something. The default value is "Rating_Inferred", but you can set it to whatever you want. If you set it to an empty string (""), the tool will not set an inferred tag. That will save significant processing time -- Plex seems to take 20x-50x longer to save the tag than the numeric rating. But it also removes the capability mentioned above, and also the possibility to restore the plex_state.json file if it's every lost/corrupted.

**DYNAMIC_PRECISION** : A true/false value, when true it will allow an item's rating to drift a little before updating it. This can save significant processing time on later runs.

**COOLDOWN_BATCH** : This and the corresponding `SLEEP` setting are intended to let the server get a very brief rest so it can settle if necessary. The `BATCH` value specifies how many items to update before taking a break.

**COOLDOWN_SLEEP** : This works together with the `BATCH` setting. The `SLEEP` value specifies how many seconds to wait.

**ALBUM_INHERITANCE_GRAVITY** and **TRACK_INHERITANCE_GRAVITY**
: It would be pretty surprising if a 5-star artist's albums were *all* 5-stars, or that a 5-star album's tracks were *all* 5-stars. When propagating ratings downwards, these settings control how much the global average "pulls" the inherited *manual* rating towards it. A value of 0 means direct inheritance (e.g., a 5-star album makes all its unrated tracks 5-stars). A value of 1 means the global average is inherited. This doesn't affect calculations when the parent's rating is inferred, since those should already have a discount baked in by way of the `CONFIDENCE_C` factor.

**BULK_ARTIST_FILENAME**, **BULK_ALBUM_FILENAME**, and **BULK_TRACK_FILENAME**

: blah blah

### TWIN_LOGIC

**ENABLED**

: You can disable the twin updating logic by setting this to false. That would let you keep using the "0" option to run the whole cycle in one shot.

**DURATION_TOLERANCE_SEC**

: It's not uncommon for different releases to vary by a couple of seconds, but if the difference in length of two tracks is greater than this setting, they won't be considered twins.

**EXCLUDE_KEYWORDS**

: It really annoys me when Plex decides that a live recording of a song is the same as the studio original, and plays it instead. This setting helps detect that by eliminating tracks from consideration because they've got a name like "live". This applies to both the title of the track *and* to the title of the album.

**EXCLUDE_PARENTHESES**

: If a track's name includes parentheses, and this setting is true, then the parens are assumed to indicate that it's some variation of the original, i.e., not a twin.

**EXCLUDE_LIVE_ALBUMS**

: Similar to the EXCLUDE_KEYWORDS setting, this attempts to detect tracks on albums that Plex considers to be live.

**TWIN_TAG**

: If set to a non-empty string, all tracks discovered to be twins will be given a tag with this name.

### UPWARD_EXCLUSION_RULES

ENABLED": true,

MIN_DURATION_SEC": 80,

KEYWORDS": ["intro", "outro", "interview", "skit", "applause", "commentary"],

CASE_SENSITIVE": false






