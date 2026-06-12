from __future__ import annotations

import contextlib
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

from litsync.utils import human_bytes, partial_note

console = Console()


class UI:
    """Plain-text fallback UI (no colors, no progress bars)."""

    def planning(self, name: str):
        print(f"Planning {name} ...")

    def planned(self, total: int, sources: int):
        print(f"Planned {total} files across {sources} source groups")

    @contextlib.contextmanager
    def metadata_progress(self, total: int):
        yield _NoopProgress(total)

    @contextlib.contextmanager
    def download_progress(self):
        yield _NoopDownloadProgress()

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


class _NoopProgress:
    def __init__(self, total: int):
        self.total = total

    def advance(self, n: int = 1):
        pass

    def set_description(self, desc: str):
        pass


class _NoopDownloadProgress:
    def add_task(self, description: str, total: Optional[int]) -> int:
        return 0

    def update(self, task_id: int, advance: int = 0, completed: Optional[int] = None,
               description: Optional[str] = None):
        pass


class RichUI(UI):
    """Rich-based progress bars and tables."""

    def planning(self, name: str):
        console.print(f"[cyan]Planning[/cyan] {name} ...")

    def planned(self, total: int, sources: int):
        console.print(f"[green]Planned {total} files across {sources} source groups[/green]")

    @contextlib.contextmanager
    def metadata_progress(self, total: int):
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            console=console,
            transient=True,
        )
        task = progress.add_task("Checking metadata...", total=total)
        try:
            with progress:
                yield _RichProgress(task, progress)
        finally:
            pass

    @contextlib.contextmanager
    def download_progress(self):
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            "[progress.percentage]{task.percentage:>3.0f}%",
            " ",
            DownloadColumn(),
            " ",
            TransferSpeedColumn(),
            " ",
            TimeRemainingColumn(),
            console=console,
        )
        try:
            with progress:
                yield _RichDownloadProgress(progress)
        finally:
            pass

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


class _RichProgress:
    def __init__(self, task: TaskID, progress: Progress):
        self.task = task
        self.progress = progress

    def advance(self, n: int = 1):
        self.progress.advance(self.task, n)

    def set_description(self, desc: str):
        self.progress.update(self.task, description=desc)


class _RichDownloadProgress:
    def __init__(self, progress: Progress):
        self.progress = progress

    def add_task(self, description: str, total: Optional[int]) -> int:
        return self.progress.add_task(description, total=total)

    def update(self, task_id: int, advance: int = 0, completed: Optional[int] = None,
               description: Optional[str] = None):
        kwargs = {"advance": advance}
        if completed is not None:
            kwargs["completed"] = completed
        if description is not None:
            kwargs["description"] = description
        self.progress.update(task_id, **kwargs)


def new_src_stats() -> dict:
    return {"new": 0, "existing": 0, "skipped": 0,
            "downloaded": 0, "verified": 0, "failed": 0, "bytes": 0, "articles": 0}
