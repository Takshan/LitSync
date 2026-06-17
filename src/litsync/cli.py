from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from rich.logging import RichHandler

from litsync.config import Config
from litsync.extract import run_extraction
from litsync.sync import Syncer
from litsync.ui import RichUI, UI, console, print_banner
from litsync.utils import run_lock


LOG = logging.getLogger("litsync")


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"litsync_{__import__('datetime').date.today().isoformat()}.log"

    file_handler = logging.FileHandler(logfile)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    file_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Must reuse the shared console so Rich's Live display and logging don't
    # fight over the terminal: two different Console instances break Live's
    # cursor-tracking and cause every refresh to print as a new block.
    rich_handler = RichHandler(console=console, rich_tracebacks=True, show_path=False)
    rich_handler.setFormatter(logging.Formatter("%(message)s"))
    # Suppress per-file INFO noise from the terminal; it still goes to the log file.
    rich_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[rich_handler, file_handler],
    )


def parse_args(argv: Optional[list[str]] = None) -> Config:
    ap = argparse.ArgumentParser(
        prog="litsync",
        description="Incremental mirror for PubMed, PMC, FDA, and ClinicalTrials.gov",
    )
    ap.add_argument("--data-root", required=True, type=Path,
                    help="root directory for the local mirror")
    ap.add_argument("--email", default=os.environ.get("NCBI_EMAIL", ""),
                    help="contact email (sent in User-Agent)")
    ap.add_argument("--sources", nargs="+", default=["pubmed", "pmc"],
                    choices=["pubmed", "pmc", "fda", "clinicaltrials"])
    ap.add_argument("--pmc-groups", nargs="+", default=["oa_comm", "oa_noncomm", "oa_other"],
                    choices=["oa_comm", "oa_noncomm", "oa_other"])
    ap.add_argument("--pmc-formats", nargs="+", default=["xml"],
                    choices=["xml", "txt"])
    ap.add_argument("--fda-endpoints", nargs="+", default=None,
                    help="openFDA endpoints to mirror, e.g. 'drug/event drug/label'; default: all")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-retries", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true",
                    help="plan only, download nothing")
    ap.add_argument("--reverify", action="store_true",
                    help="re-download already-downloaded files to verify integrity")
    ap.add_argument("--prune", action="store_true",
                    help="delete local files no longer present on the server")
    ap.add_argument("--count-articles", action="store_true",
                    help="count articles in already-downloaded local files and exit "
                         "(no network); backfills the per-source article totals")
    ap.add_argument("--no-rich", action="store_true",
                    help="disable Rich progress bars and use plain text output")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="enable debug logging")
    a = ap.parse_args(argv)
    if not a.email:
        ap.error("provide --email or set NCBI_EMAIL")
    return Config(
        data_root=a.data_root.expanduser().resolve(),
        email=a.email,
        sources=tuple(a.sources),
        pmc_groups=tuple(a.pmc_groups),
        pmc_formats=tuple(a.pmc_formats),
        workers=max(1, a.workers),
        max_retries=a.max_retries,
        timeout=a.timeout,
        dry_run=a.dry_run,
        reverify=a.reverify,
        prune=a.prune,
        count_articles=a.count_articles,
        fda_endpoints=tuple(a.fda_endpoints) if a.fda_endpoints else None,
        verbose=a.verbose,
    )


def sync_command(cfg: Config, ui: UI) -> int:
    cfg.data_root.mkdir(parents=True, exist_ok=True)
    setup_logging(cfg.log_dir, verbose=cfg.verbose)
    LOG.info("litsync starting | root=%s sources=%s dry_run=%s count_articles=%s",
             cfg.data_root, cfg.sources, cfg.dry_run, cfg.count_articles)
    with run_lock(cfg.lock_path):
        syncer = Syncer(cfg, ui)
        if cfg.count_articles:
            return syncer.backfill_counts()
        return syncer.run()


def extract_command(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="litsync-extract",
        description="Extract litsync mirror into sharded JSONL",
    )
    ap.add_argument("--data-root", type=Path, default=Path("./data/literature"))
    ap.add_argument("--out", type=Path, default=Path("./data/corpus"))
    ap.add_argument("--sources", nargs="+", default=["pubmed", "pmc"],
                    choices=["pubmed", "pmc", "fda", "clinicaltrials"])
    ap.add_argument("--shard-size-mb", type=int, default=256)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    run_extraction(
        args.data_root.expanduser().resolve(),
        args.out.expanduser().resolve(),
        args.sources,
        args.shard_size_mb,
        args.limit,
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    # Pre-scan argv for --no-rich so we can choose the UI before argparse runs.
    raw = argv if argv is not None else sys.argv[1:]
    use_plain = "--no-rich" in raw
    cfg = parse_args(argv)
    # Fall back to plain UI if stdout is not a real terminal (live progress won't work).
    if not use_plain and not console.is_terminal:
        use_plain = True
    ui = UI() if use_plain else RichUI()
    if not use_plain:
        print_banner()
    return sync_command(cfg, ui)


if __name__ == "__main__":
    raise SystemExit(main())
