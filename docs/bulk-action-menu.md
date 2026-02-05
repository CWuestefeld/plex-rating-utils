## Bulk Actions Menu

For anything other than rating individual items, especially while they're playing, or the tracks in an album, rating things in Plex is kind of a pain. The bulk action tools allow you to export *all* the current ratings as CSV files for editing *en masse*.

There are six actions here for import and export of artists, albums, and tracks. All of these will prompt you for the file to act on, and then go do its thing.

Once the data is saved as a CSV file, you can open it in your favorite spreadsheet app (or a text editor, but that's pretty clunky). There's a bunch of columns that are provided to give you context for which record is which:

**Artists**: ratingKey, artistName, sortName, albumCount, genres, userRating, ratingType

**Albums**: ratingKey, albumName, sortName, artistName, releaseYear, genres, userRating, ratingType

**Tracks**: ratingKey, trackTitle, trackArtist, albumName, albumArtist, userRating, ratingType

For all of these columns, the only things that matter at all when you're importing the data are **ratingKey**, **userRating**, and **ratingType**. All the other values are ignored. However, when importing later, the app will check that all of those column headings are present in row #1 as a safety check to ensure it's operating on the right data. You are free  to add additional columns to the right if you want, that will all be ignored.

**ratingKey**

: *Don't change this*, treat it as sacred. That's the identifier for the record on that row. If you change it, if you're lucky it'll just give you an error that the record wasn't found. If you're not lucky, you'll update the wrong record.

**userRating**

: This is the rating value. Change it as you wish. Note that while Plex limits you to entering ratings with whole, or optionally half, stars, you can actually enter any number you want in the 0.0-5.0 range. Something like 3.68 is a perfectly acceptable rating.

**ratingType**

: this must be either `manual` or `inferred`. Normally you'll want to set this to **manual** for anything you've changed, well, manually. Setting it to **inferred** is effectively saying that you're withdrawing your own rating opinion, and letting the program infer the values for you. If you set it to inferred, it doesn't really matter in the long term what userRating you give, because that will all get recalculated the next time you run the inference engine anyway.

**releaseYear**

: This is a special case just for Albums. If you like, you can update the album's year by changing this value. (This is separate from ratings, but because the data Plex fetched for my album's years is generally dreadful, and it was easy to implement this, I decided to go ahead.)

### Notes

Importing data like this takes just as much time as running the inference engine (the computation is trivial compared to the performance impact of storing the data). So if you're changing lots of tracks, and you're using tagging, this will be time consuming.
