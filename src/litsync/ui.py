from __future__ import annotations

import contextlib
import threading
from collections import deque
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from litsync.utils import human_bytes, partial_note

_CUSTOM_THEME = Theme(
    {
        "info": "cyan",
        "success": "bold green",
        "warning": "bold yellow",
        "error": "bold red",
        "dim": "dim",
        "title": "bold bright_cyan",
        "accent": "bright_magenta",
    }
)

console = Console(theme=_CUSTOM_THEME, highlight=False)


class UI:
    """Plain-text fallback UI (no colors, no progress bars)."""

    def planning(self, name: str):
        print(f"Planning {name} ...")

    def planned(self, total: int, sources: int):
        print(f"Planned {total} files across {sources} source groups")

    @contextlib.contextmanager
    def run_progress(self, total: int):
        yield _NoopRunProgress(total)

    @contextlib.contextmanager
    def extract_progress(self, total: int):
        print(f"Extracting {total} files ...")
        yield _NoopExtractProgress(total)

    def extract(self, rel_path: str):
        print(f"Extracting {rel_path}")

    def summary(self, started: str, finished: str, stats: dict, per_source: dict,
                mirror: dict, source_urls: dict, bytes_downloaded: int, articles_downloaded: int):
        new_dl = stats.get("verified", 0) + stats.get("downloaded", 0)
        print("litsync run complete")
        print(f"  started: {started}")
        print(f"  finished: {finished}")
        print(f"  newly downloaded: {new_dl}, skipped: {stats.get('skipped', 0)}, failed: {stats.get('failed', 0)}")
        print(f"  bytes: {human_bytes(bytes_downloaded)}, articles: {articles_downloaded:,}")

    def extract_summary(self, started: str, finished: str, stats: dict):
        if stats.get("interrupted"):
            print("litsync-extract interrupted by user")
        else:
            print("litsync-extract complete")
        print(f"  started: {started}")
        print(f"  finished: {finished}")
        for key in ("pubmed", "pmc", "fda", "clinicaltrials"):
            files = stats.get(f"{key}_files", 0)
            skipped = stats.get(f"{key}_skipped", 0)
            records = stats.get(f"{key}_records", 0)
            if files or skipped or records:
                print(f"  {key}: {files} files, {records} records", end="")
                if skipped:
                    print(f" ({skipped} skipped)", end="")
                print()
        skipped_total = stats.get("skipped_files", 0)
        if skipped_total:
            print(f"  skipped files: {skipped_total}")
        print(f"  errors: {stats.get('errors', 0)}")
        print(f"  total records: {stats.get('total_records', 0):,}")
        print(f"  shards: {stats.get('shards', 0)}")
        if stats.get("yearly"):
            print("  per-year:")
            for year, info in sorted(stats.get("years", {}).items()):
                print(f"    {year}: {info.get('records', 0):,} records, {info.get('shards', 0)} shards")
        print(f"  elapsed: {stats.get('elapsed_sec', 0):.1f}s")
        print(f"  output: {stats.get('out_dir', '')}")


class _NoopRunProgress:
    def __init__(self, total: int):
        self.total = total

    def file_done(self):
        pass

    def file_failed(self):
        pass

    def add_download(self, description: str, total: Optional[int]) -> int:
        return 0

    def advance_download(self, task_id: int, nbytes: int):
        pass

    def close_download(self, task_id: int):
        pass


class _NoopExtractProgress:
    def __init__(self, total: int):
        self.total = total

    def set_current(self, rel_path: str, records: int):
        pass

    def file_done(self):
        pass

    def file_failed(self):
        pass

    def file_skipped(self):
        pass


class _RichRunProgress:
    """Single live display: overall progress bar + per-file bars. Completed files vanish immediately."""

    MAX_VISIBLE = 3

    def __init__(self, total: int):
        self._lock = threading.Lock()
        self._done = 0
        self._failed = 0
        self._active = 0
        self._visible: deque[TaskID] = deque()
        self._hidden: set[TaskID] = set()

        self._overall = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]syncing"),
            BarColumn(bar_width=40),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.completed:,}/{task.total:,} files"),
            TextColumn("{task.fields[extra]}"),
            TimeElapsedColumn(),
            console=console,
        )
        self._overall_task = self._overall.add_task("files", total=max(total, 1), extra="")

        self._files = Progress(
            TextColumn("  [blue]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            DownloadColumn(),
            TransferSpeedColumn(),
            console=console,
        )

        self._live = Live(
            Group(self._overall, self._files),
            console=console,
            refresh_per_second=8,
            transient=True,
        )

    def __enter__(self) -> "_RichRunProgress":
        self._live.__enter__()
        return self

    def __exit__(self, *exc_info) -> None:
        self._live.__exit__(*exc_info)

    def _refresh(self) -> None:
        extra = f"[success]{self._done:,} ok[/success]"
        if self._failed:
            extra += f"  [error]{self._failed:,} failed[/error]"
        if self._active:
            extra += f"  [dim]{self._active} active[/dim]"
        self._overall.update(
            self._overall_task,
            completed=self._done + self._failed,
            extra=extra,
        )

    def file_done(self) -> None:
        with self._lock:
            self._done += 1
            self._refresh()

    def file_failed(self) -> None:
        with self._lock:
            self._failed += 1
            self._refresh()

    def add_download(self, description: str, total: Optional[int]) -> TaskID:
        with self._lock:
            self._active += 1
            task_id = self._files.add_task(description, total=total)
            self._promote(task_id)
            self._refresh()
        return task_id

    def _promote(self, task_id: TaskID) -> None:
        self._hidden.discard(task_id)
        if task_id not in self._visible:
            self._visible.append(task_id)
        self._files.update(task_id, visible=True)
        while len(self._visible) > self.MAX_VISIBLE:
            oldest = self._visible.popleft()
            self._files.update(oldest, visible=False)
            self._hidden.add(oldest)

    def advance_download(self, task_id: TaskID, nbytes: int) -> None:
        self._files.update(task_id, advance=nbytes)

    def close_download(self, task_id: TaskID) -> None:
        with self._lock:
            self._active -= 1
            with contextlib.suppress(KeyError):
                self._files.remove_task(task_id)
            if task_id in self._visible:
                self._visible.remove(task_id)
            self._hidden.discard(task_id)
            while self._hidden and len(self._visible) < self.MAX_VISIBLE:
                promoted = min(self._hidden)
                self._hidden.remove(promoted)
                self._visible.append(promoted)
                self._files.update(promoted, visible=True)
            self._refresh()


class _RichExtractProgress:
    """Single live display: overall extraction progress + current file label."""

    def __init__(self, total: int):
        self._lock = threading.Lock()
        self._done = 0
        self._failed = 0
        self._skipped = 0

        self._overall = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]extracting"),
            BarColumn(bar_width=40),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.completed:,}/{task.total:,} files"),
            TextColumn("{task.fields[extra]}"),
            TimeElapsedColumn(),
            console=console,
        )
        self._overall_task = self._overall.add_task("files", total=max(total, 1), extra="")

        self._current = Progress(
            TextColumn("  [blue]{task.description}"),
            console=console,
        )
        self._current_task = self._current.add_task("waiting ...", total=None)

        self._live = Live(
            Group(self._overall, self._current),
            console=console,
            refresh_per_second=8,
            transient=True,
        )

    def __enter__(self) -> "_RichExtractProgress":
        self._live.__enter__()
        return self

    def __exit__(self, *exc_info) -> None:
        self._live.__exit__(*exc_info)

    def _refresh(self) -> None:
        extra = f"[success]{self._done:,} ok[/success]"
        if self._skipped:
            extra += f"  [dim]{self._skipped:,} skipped[/dim]"
        if self._failed:
            extra += f"  [error]{self._failed:,} failed[/error]"
        self._overall.update(
            self._overall_task,
            completed=self._done + self._failed + self._skipped,
            extra=extra,
        )

    def set_current(self, rel_path: str, records: int):
        with self._lock:
            self._current.update(
                self._current_task,
                description=f"{rel_path}  ({records:,} records)",
            )

    def file_done(self) -> None:
        with self._lock:
            self._done += 1
            self._refresh()

    def file_failed(self) -> None:
        with self._lock:
            self._failed += 1
            self._refresh()

    def file_skipped(self) -> None:
        with self._lock:
            self._skipped += 1
            self._refresh()


class RichUI(UI):
    """Rich-based progress bars and tables."""

    def planning(self, name: str):
        console.print(f"[cyan]Planning[/cyan] {name} ...")

    def planned(self, total: int, sources: int):
        console.print(f"[green]Planned {total} files across {sources} source groups[/green]")

    @contextlib.contextmanager
    def run_progress(self, total: int):
        rp = _RichRunProgress(total)
        with rp:
            yield rp

    @contextlib.contextmanager
    def extract_progress(self, total: int):
        ep = _RichExtractProgress(total)
        with ep:
            yield ep

    def extract(self, rel_path: str):
        console.print(f"[yellow]Extracting[/yellow] {rel_path}")

    def summary(self, started: str, finished: str, stats: dict, per_source: dict,
                mirror: dict, source_urls: dict, bytes_downloaded: int, articles_downloaded: int):
        new_dl = stats.get("verified", 0) + stats.get("downloaded", 0)
        table = Table(title="litsync summary", show_header=True, header_style="bold magenta")
        table.add_column("Source", style="cyan")
        table.add_column("Files", justify="right")
        table.add_column("Size", justify="right")
        table.add_column("Records", justify="right")
        table.add_column("Run", justify="left")

        labels = {
            "pubmed": "PubMed",
            "pmc": "PubMed Central",
            "fda": "openFDA",
            "clinicaltrials": "ClinicalTrials.gov",
        }

        families: dict[str, list[str]] = {}
        sources = sorted(set(mirror) | set(per_source))
        for source in sources:
            families.setdefault(source.split("_", 1)[0], []).append(source)

        grand_files = grand_bytes = grand_articles = grand_counted = 0
        for fam in sorted(families):
            fam_files = fam_bytes = fam_articles = fam_counted = 0
            for source in families[fam]:
                m = mirror.get(source, {"files": 0, "bytes": 0, "articles": 0, "counted": 0})
                r = per_source.get(source, new_src_stats())
                got = r.get("verified", 0) + r.get("downloaded", 0)
                if m["counted"]:
                    art = f"{m['articles']:,}"
                    if m["counted"] < m["files"]:
                        art += f" (counted {m['counted']}/{m['files']})"
                else:
                    art = "—"
                run_txt = f"+{r.get('new', 0)} new, {got} fetched, {r.get('skipped', 0)} current, {r.get('failed', 0)} failed"
                table.add_row(source, str(m["files"]), human_bytes(m["bytes"]), art, run_txt)
                fam_files += m["files"]
                fam_bytes += m["bytes"]
                fam_articles += m["articles"]
                fam_counted += m["counted"]
            table.add_row(
                f"[bold]{labels.get(fam, fam)} subtotal[/bold]",
                str(fam_files),
                human_bytes(fam_bytes),
                f"{fam_articles:,}{partial_note(fam_counted, fam_files)}",
                "",
            )
            grand_files += fam_files
            grand_bytes += fam_bytes
            grand_articles += fam_articles
            grand_counted += fam_counted

        table.add_row(
            "[bold]TOTAL[/bold]",
            str(grand_files),
            human_bytes(grand_bytes),
            f"{grand_articles:,}{partial_note(grand_counted, grand_files)}",
            f"+{new_dl} downloaded, {stats.get('skipped', 0)} skipped, {stats.get('failed', 0)} failed",
            style="bold green",
        )
        console.print()
        console.print(table)
        console.print(
            f"[dim]started: {started}  ·  finished: {finished}  · "
            f" bytes this run: {human_bytes(bytes_downloaded)}  · "
            f" records this run: {articles_downloaded:,}[/dim]"
        )
        if grand_counted < grand_files:
            console.print(
                "[dim]Run --count-articles to backfill record counts for already-downloaded files.[/dim]"
            )

    def extract_summary(self, started: str, finished: str, stats: dict):
        title = "litsync-extract summary"
        if stats.get("interrupted"):
            title += " [INTERRUPTED]"
        table = Table(title=title, show_header=True, header_style="bold magenta")
        table.add_column("Source", style="cyan")
        table.add_column("Files", justify="right")
        table.add_column("Skipped", justify="right")
        table.add_column("Records", justify="right")
        table.add_column("Errors", justify="right")

        labels = {
            "pubmed": "PubMed",
            "pmc": "PubMed Central",
            "fda": "openFDA",
            "clinicaltrials": "ClinicalTrials.gov",
        }

        grand_files = grand_skipped = grand_records = grand_errors = 0
        for key in ("pubmed", "pmc", "fda", "clinicaltrials"):
            files = stats.get(f"{key}_files", 0)
            skipped = stats.get(f"{key}_skipped", 0)
            records = stats.get(f"{key}_records", 0)
            errors = stats.get(f"{key}_errors", 0) if f"{key}_errors" in stats else 0
            if files == 0 and skipped == 0 and records == 0 and errors == 0:
                continue
            table.add_row(
                labels.get(key, key),
                f"{files:,}",
                f"{skipped:,}" if skipped else "—",
                f"{records:,}",
                f"{errors:,}" if errors else "—",
            )
            grand_files += files
            grand_skipped += skipped
            grand_records += records
            grand_errors += errors

        table.add_row(
            "[bold]TOTAL[/bold]",
            f"{grand_files:,}",
            f"{grand_skipped:,}" if grand_skipped else "—",
            f"{grand_records:,}",
            f"{grand_errors:,}" if grand_errors else "—",
            style="bold green",
        )
        console.print()
        console.print(table)
        console.print(
            f"[dim]started: {started}  ·  finished: {finished}  · "
            f" shards: {stats.get('shards', 0)}  · "
            f" total records: {stats.get('total_records', 0):,}  · "
            f" elapsed: {stats.get('elapsed_sec', 0):.1f}s  · "
            f" output: {stats.get('out_dir', '')}[/dim]"
        )
        if stats.get("yearly"):
            console.print()
            year_table = Table(title="per-year breakdown", show_header=True, header_style="bold magenta")
            year_table.add_column("Year", style="cyan")
            year_table.add_column("Records", justify="right")
            year_table.add_column("Shards", justify="right")
            for year, info in sorted(stats.get("years", {}).items()):
                year_table.add_row(
                    year,
                    f"{info.get('records', 0):,}",
                    f"{info.get('shards', 0):,}",
                )
            console.print(year_table)


def print_banner() -> None:
    text = Text()
    text.append(" ██╗     ██╗████████╗███████╗██╗   ██╗███╗   ██╗ ██████╗\n", style="bold bright_cyan")
    text.append(" ██║     ██║╚══██╔══╝██╔════╝██║   ██║████╗  ██║██╔════╝\n", style="bold bright_cyan")
    text.append(" ██║     ██║   ██║   ███████╗████████║██╔██╗ ██║██║     \n", style="bold bright_cyan")
    text.append(" ██║     ██║   ██║   ╚════██║╚══██╔══╝██║╚██╗██║██║     \n", style="bold bright_cyan")
    text.append(" ███████╗██║   ██║   ███████║   ██║   ██║ ╚████║╚██████╗\n", style="bold bright_cyan")
    text.append(" ╚══════╝╚═╝   ╚═╝   ╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝ ", style="bold bright_cyan")
    console.print(
        Panel(
            text,
            title="[bold]LitSync[/bold]",
            subtitle="[dim]incremental mirror for biomedical literature[/dim]",
            border_style="bright_cyan",
            padding=(0, 2),
        )
    )


def new_src_stats() -> dict:
    return {"new": 0, "existing": 0, "skipped": 0,
            "downloaded": 0, "verified": 0, "failed": 0, "bytes": 0, "articles": 0}
