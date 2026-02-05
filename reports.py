import math
from collections import Counter, defaultdict
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich.bar import Bar
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

class LibraryCache:
    def __init__(self, music):
        self.music = music
        self._tracks = None
        self._albums = None
        self._artists = None

    def get_tracks(self):
        if self._tracks is None:
            self._tracks = self.music.searchTracks()
        return self._tracks

    def get_albums(self):
        if self._albums is None:
            self._albums = self.music.searchAlbums()
        return self._albums

    def get_artists(self):
        if self._artists is None:
            self._artists = self.music.searchArtists()
        return self._artists

    def clear(self):
        self._tracks = None
        self._albums = None
        self._artists = None

def show_library_coverage(cache, state):
    """Report A: Library Coverage ('The Void')"""
    console.clear()
    console.rule("[bold blue]Report: Library Coverage")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True
    ) as progress:
        progress.add_task("Scanning library statistics...", total=None)
        
        # Fetch counts
        progress.add_task("Scanning tracks (this is the big one)...", total=None)
        total_tracks = len(cache.get_tracks())
        progress.add_task("Scanning albums...", total=None)
        total_albums = len(cache.get_albums())
        progress.add_task("Scanning artists...", total=None)
        total_artists = len(cache.get_artists())

        count_total = total_tracks + total_albums + total_artists
        count_inferred = 0
        count_twin = 0

        count_inferred = len(state.keys())
        count_manual = count_total - count_inferred
        
        for entry in state.values():
            if isinstance(entry, dict) and entry.get('t', 0) > 0:
                count_twin += 1

    # Render Table
    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Percentage", justify="right")
    table.add_column("Visual", width=40)

    def add_row(label, value, total, color):
        pct = (value / total * 100) if total > 0 else 0
        bar = Bar(total, 0, value, width=40, color=color)
        table.add_row(label, f"{value:,}", f"{pct:.1f}%", bar)

    add_row("Total Items", count_total, count_total, "black")
    add_row("Total Tracks", total_tracks, count_total, "cyan")
    add_row("Total Albums", total_albums, count_total, "blue")
    add_row("Total Artists", total_artists, count_total, "purple")
    add_row("Manual Ratings", count_manual, count_total, "green")
    add_row("Inferred Ratings", count_inferred, count_total, "yellow")
    add_row("Twin-Linked", count_twin, count_total, "magenta")

    console.print(table)
    console.print(f"\n[dim]Total Library Size: {count_total:,} items[/dim]\n")
    input("Press Enter to continue...")

def show_rating_histogram(cache, state):
    """Report B: Rating Histogram"""
    console.clear()
    console.rule("[bold blue]Report B: Rating Histogram")

    with Progress(SpinnerColumn(), TextColumn("Analyzing rating distribution..."), transient=True) as progress:
        progress.add_task("scan")
        rated_tracks = cache.get_tracks()
        
        manual_buckets = Counter()
        inferred_buckets = Counter()
        
        for track in rated_tracks:
            if not track.userRating: continue
            key = str(track.ratingKey)
            rating = track.userRating or 0.0
            # Snap to nearest 0.5 (Plex uses 0-10 scale, so we divide by 2)
            # Actually, let's keep 0-10 scale for internal math but display as stars (0-5)
            stars = round((rating / 2.0) * 2) / 2.0  # Round to nearest 0.5
            
            if key in state:
                inferred_buckets[stars] += 1
            else:
                manual_buckets[stars] += 1

    # Determine max width for scaling
    all_counts = manual_buckets + inferred_buckets
    if not all_counts:
        console.print("No rated tracks found.")
        input("Press Enter...")
        return

    max_count = max(all_counts.values())
    max_bar_width = 50
    total_items = sum(all_counts.values())

    # Use a table for alignment
    table = Table(box=None, padding=(0, 2), show_header=True)
    table.add_column("Rating", justify="right", style="cyan")
    table.add_column("Distribution", style="white")
    table.add_column("Count", justify="right", style="green")
    table.add_column("Pct", justify="right", style="yellow")

    # Iterate 5.0 down to 0.5
    for i in range(10, 0, -1):
        stars = i / 2.0
        m_count = manual_buckets.get(stars, 0)
        i_count = inferred_buckets.get(stars, 0)
        total = m_count + i_count
        
        if total == 0: continue

        # Calculate bar segments
        total_width = int((total / max_count) * max_bar_width)
        if total_width == 0 and total > 0: total_width = 1
        
        m_width = int((m_count / total) * total_width)
        i_width = total_width - m_width
        
        # Correct for integer truncation issues, ensuring visibility of small segments.
        if total_width == 1 and m_count > 0 and i_count > 0:
            # For a single-char bar with mixed content, give it to the majority.
            if i_count > m_count:
                i_width = 1
                m_width = 0
            # In a tie, the original calculation (m_width=0, i_width=1) is preserved
            # unless we explicitly override, so we'll let manual win the tie.
            else: # m_count >= i_count
                m_width = 1
                i_width = 0
        elif m_count > 0 and m_width == 0:
            m_width = 1
            i_width = max(0, total_width - 1)
        elif i_count > 0 and i_width == 0:
            i_width = 1
            m_width = max(0, total_width - 1)
        bar_str = f"[green]{'█' * m_width}[/green][yellow]{'░' * i_width}[/yellow]"
        
        pct = (total / total_items) * 100
        table.add_row(f"{stars:.1f}", bar_str, f"{total}", f"{pct:.1f}%")

    console.print(table)
    console.print("\n[green]█ Manual[/green]  [yellow]░ Inferred[/yellow]")
    input("\nPress Enter to continue...")

def show_twins_inventory(clusters):
    """Report C: Twins Inventory"""
    console.clear()
    console.rule("[bold blue]Report C: Twins Inventory")

    if not clusters:
        console.print("No twin clusters found. Run Phase 5 logic first or ensure Twin Logic is enabled.")
        input("Press Enter...")
        return

    # Sort clusters by Artist Name of the first item
    clusters.sort(key=lambda c: c[0]['item'].grandparentTitle or "Unknown")

    tree = Tree("[bold magenta]Twin Clusters Registry[/bold magenta]")

    for cluster in clusters:
        if len(cluster) < 2: continue
        
        # Representative info
        first = cluster[0]['item']
        artist = first.grandparentTitle or "Unknown"
        title = first.title
        
        # Calculate cluster rating (average of manual anchors or all)
        manuals = [t for t in cluster if t['is_manual']]
        if manuals:
            rating = sum(t['rating'] for t in manuals) / len(manuals)
            source = "Manual Anchor"
        else:
            rating = sum(t['rating'] for t in cluster) / len(cluster)
            source = "Inferred Consensus"
            
        node = tree.add(f"[bold]{artist} - {title}[/bold] [yellow]({rating/2:.2f}★)[/yellow] [dim]({source})[/dim]")
        
        for t in cluster:
            item = t['item']
            album = item.parentTitle or "Unknown"
            duration = f"{item.duration // 60000}:{(item.duration // 1000) % 60:02d}"
            rtype = "[green]Manual[/green]" if t['is_manual'] else "[dim]Inferred[/dim]"
            
            node.add(f"{album} - {duration} {rtype}")

    console.print(tree)
    
    # Offer export since this can be huge
    if console.input("\nExport to text file? (y/N): ").strip().lower() == 'y':
        filename = "report_twins.txt"
        with open(filename, "w", encoding="utf-8") as f:
            from rich.console import Console
            # Use a wide width to prevent wrapping in the text file
            file_console = Console(file=f, width=console.width)
            file_console.print(tree)
        console.print(f"Exported to {filename}")
    
    input("Press Enter to continue...")

def show_dissenter_report(cache):
    """Report D: The Dissenter Report (Outliers)"""
    console.clear()
    console.rule("[bold blue]Report D: The Dissenter Report")

    limit_str = console.input("How many records to show? [dim](default: 50)[/dim]: ").strip()
    try:
        limit = int(limit_str) if limit_str else 50
    except ValueError:
        limit = 50

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True) as progress:
        # 1. Fetch all rated albums to build a lookup map
        progress.add_task("Fetching Album ratings...", total=None)
        albums = cache.get_albums()
        album_map = {str(a.ratingKey): a.userRating for a in albums if a.userRating}
        
        # 2. Fetch all rated tracks
        progress.add_task("Fetching Track ratings...", total=None)
        tracks = cache.get_tracks()
        
        dissenters = []
        
        # 3. Calculate deviations
        task = progress.add_task("Calculating deviations...", total=len(tracks))
        for track in tracks:
            progress.advance(task)
            if not track.userRating: continue
            if not track.parentRatingKey: continue
            
            pkey = str(track.parentRatingKey)
            if pkey in album_map:
                album_rating = album_map[pkey]
                track_rating = track.userRating
                delta = track_rating - album_rating # Signed delta to show direction
                
                # We care about magnitude of deviation
                if abs(delta) > 0.1:
                    dissenters.append({
                        'artist': track.grandparentTitle,
                        'title': track.title,
                        'album': track.parentTitle,
                        'track_rating': track_rating,
                        'album_rating': album_rating,
                        'delta': delta,
                        'abs_delta': abs(delta)
                    })

    # Sort by absolute deviation descending
    dissenters.sort(key=lambda x: x['abs_delta'], reverse=True)
    top_n = dissenters[:limit]

    table = Table(title=f"Top {limit} Dissenters (Tracks vs Album)", box=box.SIMPLE_HEAD)
    table.add_column("Artist", style="cyan")
    table.add_column("Track Title", style="white")
    table.add_column("Album", style="blue")
    table.add_column("Track ★", justify="right", style="green")
    table.add_column("Album ★", justify="right", style="yellow")
    table.add_column("Deviation", justify="right", style="bold red")

    for d in top_n:
        sign = "+" if d['delta'] > 0 else ""
        table.add_row(
            d['artist'],
            d['title'],
            d['album'] or "",
            f"{d['track_rating']/2:.1f}",
            f"{d['album_rating']/2:.1f}",
            f"{sign}{d['delta']/2:.1f}"
        )

    console.print(table)

    if console.input("\nExport to text file? (y/N): ").strip().lower() == 'y':
        filename = "report_dissenters.txt"
        with open(filename, "w", encoding="utf-8") as f:
            from rich.console import Console
            # Use the main console's width to ensure consistent table rendering in the file
            file_console = Console(file=f, width=console.width)
            file_console.print(table)
        console.print(f"Exported to {filename}")

    input("\nPress Enter to continue...")
