## Reporting

The tool offers four reports:

* Library Coverage

* Rating Histogram

* Twins Inventory

* Dissenter Report

There's also a `Clear Cache` option. For usability, the reporting facility holds cached the library data after the first time it retrieves it, so subsequent reports within the same session will be generated relatively quickly. But if you've made big changes, like adding a bunch of tracks to your library, while inside this tool, you may want to force it to refresh. That's when you'd want to clear the cache.

### Library Coverage

This tries to give you a very broad overview of the rating status of your library.

![image of report](./docs/report-coverage.png)

It starts off showing you what's in your library: the total number of items, and then how many of those are tracks, albums, and artists. It then shows you how many of these are manual ratings (that is, stuff you've rated yourself), how many were inferred by this app, and how many items are part of a twin relationship.

### Rating Histogram

This shows you how many tracks have what rating (rounded to the half-star).

![image of report](./docs/report-ratings-histogram.png)

Each bar represents the total number of items with that rating. The solid part of the line at the left is how many have that rating because you set it yourself, while the shaded part to the right is how many of them were inferred by the rating engine.

### Twins Inventory

This gives you a list of all the tracks that the tool identified as being twins.

![image of report](./docs/report-twins.png)

It shows the artis and the track name, the number of stars it computed, and why. Below that, it shows the albums on which that track was found to appear.

### Dissenter Report (Outliers)

This is a tool to help you find erroneous ratings based on where a given track's rating differs widely from the other tracks on the same album.

![image of report](./docs/report-dissenter.png)

You can see the Artist, Track, and Album; then the track's rating, the album's overall rating, and the deviation between those scores.




