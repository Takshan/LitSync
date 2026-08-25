from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import shutil
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from litsync.config import Config
from litsync.http import HttpClient
from litsync.sources import Task
from litsync.sources.clinicaltrials import ClinicalTrialsSource
from litsync.sources.fda import FdaSource
from litsync.sources.pmc import PmcSource
from litsync.sources.pubmed import PubMedSource
from litsync.state import FileRecord, StateDB
from litsync.ui import UI
from litsync.utils import count_articles, human_bytes, md5_file, new_src_stats, utcnow

LOG = logging.getLogger("litsync")


class Syncer:
    def __init__(self, cfg: Config, ui: UI):
        self.cfg = cfg
        self.ui = ui
        self.http = HttpClient(cfg)
        self.db = StateDB(cfg.db_path)
        self._stats_lock = threading.Lock()
        self.stats = {"skipped": 0, "downloaded": 0, "verified": 0, "failed": 0}
        self.bytes_downloaded = 0
        self.articles_downloaded = 0
        self.per_source: dict[str, dict] = {}
        self.source_urls: dict[str, str] = {}
        self.started_at = utcnow()

    # ---- per-file decision logic ---------------------------------------- #

    def _needs_work(self, task: Task) -> tuple[bool, Optional[int]]:
        row = self.db.get(task.source, task.filename)
        on_disk = task.dest.exists()

        if (row is not None and row["status"] == "verified" and on_disk
                and not self.cfg.reverify and task.immutable):
            recorded = row["remote_size"]
            if recorded is None or task.dest.stat().st_size == recorded:
                return False, recorded

        size, mtime, etag = self.http.head(task.url)
        self.db.mark(task.source, task.filename,
                     remote_size=size, remote_mtime=mtime, etag=etag)

        if (row is not None and on_disk and not self.cfg.reverify
                and row["status"] in ("done", "verified")):
            same_size = size is None or task.dest.stat().st_size == size
            same_etag = etag is None or row["etag"] == etag
            if same_size and same_etag:
                return False, size

        return True, size

    # ---- worker --------------------------------------------------------- #

    def _process(self, task: Task, rp) -> None:
        is_new = self.db.get(task.source, task.filename) is None
        self.db.upsert_seen(FileRecord(task.source, task.filename, task.url, task.rel_path))
        self._record_seen(task.source, is_new)
        try:
            needs, expected_size = self._needs_work(task)
        except Exception as exc:
            self.db.mark(task.source, task.filename, status="failed", error=str(exc))
            self._bump("failed", task.source)
            LOG.error("metadata failed for %s: %s", task.filename, exc)
            rp.file_failed()
            return

        if not needs:
            self._bump("skipped", task.source)
            rp.file_done()
            return

        if self.cfg.dry_run:
            self._bump("downloaded", task.source, expected_size or 0)
            rp.file_done()
            return

        expected_md5 = None
        if task.md5_url:
            with contextlib.suppress(Exception):
                expected_md5 = md5_file_from_url(self.http.get_text(task.md5_url))
                self.db.mark(task.source, task.filename, md5=expected_md5)

        task_id = rp.add_download(task.filename, expected_size)

        def progress_cb(nbytes: int):
            rp.advance_download(task_id, nbytes)

        try:
            attempts = (self.db.get(task.source, task.filename)["attempts"] or 0) + 1
            self.db.mark(task.source, task.filename, status="pending", attempts=attempts)
            written = self.http.download(task.url, task.dest, expected_size, progress_cb)
        except Exception as exc:
            rp.close_download(task_id)
            self.db.mark(task.source, task.filename, status="failed", error=str(exc))
            self._bump("failed", task.source)
            LOG.error("download failed for %s: %s", task.filename, exc)
            rp.file_failed()
            return

        rp.close_download(task_id)
        try:
            if expected_md5:
                actual = md5_file(task.dest)
                if actual.lower() != expected_md5.lower():
                    task.dest.unlink(missing_ok=True)
                    raise IOError(f"md5 mismatch: got {actual}, expected {expected_md5}")
                self.db.mark(task.source, task.filename,
                             status="verified", local_md5=actual, error=None)
                self._bump("verified", task.source, written)
            else:
                self.db.mark(task.source, task.filename, status="verified", error=None)
                self._bump("verified", task.source, written)

            if task.extract:
                self._extract_zip(task)
            self._count_and_store(task)
            LOG.info("ok %s", task.rel_path)
            rp.file_done()
        except Exception as exc:
            self.db.mark(task.source, task.filename, status="failed", error=str(exc))
            self._bump("failed", task.source)
            LOG.error("post-download failed for %s: %s", task.filename, exc)
            rp.file_failed()

    def _bump(self, key: str, source: Optional[str] = None, nbytes: int = 0) -> None:
        with self._stats_lock:
            self.stats[key] += 1
            self.bytes_downloaded += nbytes
            if source is not None:
                s = self.per_source.setdefault(source, new_src_stats())
                s[key] += 1
                s["bytes"] += nbytes

    def _record_seen(self, source: str, is_new: bool) -> None:
        with self._stats_lock:
            s = self.per_source.setdefault(source, new_src_stats())
            s["new" if is_new else "existing"] += 1

    def _extract_zip(self, task: Task) -> None:
        if not task.dest.exists():
            return
        name = task.dest.name
        if name.endswith(".json.zip"):
            extract_dir = task.dest.parent / name[:-9]
        else:
            extract_dir = task.dest.with_suffix("")
        if extract_dir.exists():
            zip_mtime = task.dest.stat().st_mtime
            dir_mtime = max(
                (p.stat().st_mtime for p in extract_dir.rglob("*") if p.is_file()),
                default=0,
            )
            if dir_mtime >= zip_mtime:
                return
            shutil.rmtree(extract_dir, ignore_errors=True)
        self.ui.extract(task.rel_path)
        extract_dir.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(task.dest, "r") as zf:
            zf.extractall(extract_dir)

    def _count_and_store(self, task: Task) -> None:
        if self.cfg.dry_run:
            return
        try:
            n = count_articles(task.dest, task.source)
        except Exception as exc:
            LOG.warning("article count failed for %s: %s", task.filename, exc)
            return
        self.db.mark(task.source, task.filename, article_count=n)
        with self._stats_lock:
            self.per_source.setdefault(task.source, new_src_stats())["articles"] += n
            self.articles_downloaded += n

    # ---- prune ---------------------------------------------------------- #

    def _prune(self, tasks_by_source: dict[str, list[Task]],
               skip: set[str] = frozenset()) -> None:
        remote_by_source = {src: {t.filename for t in tasks}
                            for src, tasks in tasks_by_source.items()}
        synced_families = set(self.cfg.sources)

        def family(source: str) -> str:
            return source.split("_", 1)[0]

        for source in self.db.all_sources():
            if source in skip:
                continue
            if family(source) not in synced_families:
                continue
            remote = remote_by_source.get(source, set())
            for fname in self.db.known_filenames(source) - remote:
                row = self.db.get(source, fname)
                if row and row["rel_path"]:
                    p = self.cfg.data_root / row["rel_path"]
                    if p.exists():
                        LOG.info("prune (no longer remote): %s", row["rel_path"])
                        if not self.cfg.dry_run:
                            p.unlink(missing_ok=True)

    # ---- run ------------------------------------------------------------ #

    def run(self) -> int:
        planners = []
        if "pubmed" in self.cfg.sources:
            planners.append(PubMedSource(self.cfg, self.http))
        if "pmc" in self.cfg.sources:
            planners.append(PmcSource(self.cfg, self.http))
        if "fda" in self.cfg.sources:
            planners.append(FdaSource(self.cfg, self.http))
        if "clinicaltrials" in self.cfg.sources:
            planners.append(ClinicalTrialsSource(self.cfg, self.http))

        all_tasks: list[Task] = []
        by_source: dict[str, list[Task]] = {}
        no_prune: set[str] = set()
        for p in planners:
            self.ui.planning(type(p).__name__)
            tasks = p.plan()
            if not getattr(p, "full_enumeration", True):
                no_prune.update(getattr(p, "source_names", ()))
            all_tasks.extend(tasks)
            for t in tasks:
                by_source.setdefault(t.source, []).append(t)
                self.source_urls.setdefault(t.source, t.url.rsplit("/", 1)[0] + "/")
        self.ui.planned(len(all_tasks), len(by_source))

        with self.ui.run_progress(len(all_tasks)) as rp:
            def work(task: Task):
                self._process(task, rp)

            with ThreadPoolExecutor(max_workers=self.cfg.workers) as pool:
                futures = [pool.submit(work, t) for t in all_tasks]
                for _ in as_completed(futures):
                    pass

        if self.cfg.prune:
            self._prune(by_source, no_prune)

        self._report()
        self.db.close()
        return 1 if self.stats["failed"] else 0

    # ---- article-count backfill (no network) --------------------------- #

    def backfill_counts(self) -> int:
        rows = self.db.files_missing_counts()
        self.ui.planned(len(rows), 0)
        done = self.failed = 0

        def work(item: tuple[str, str, str]) -> None:
            nonlocal done
            source, filename, rel_path = item
            path = self.cfg.data_root / rel_path
            if not path.exists():
                return
            try:
                n = count_articles(path, source)
            except Exception as exc:
                with self._stats_lock:
                    self.failed += 1
                LOG.error("count failed for %s: %s", rel_path, exc)
                return
            self.db.mark(source, filename, article_count=n)
            with self._stats_lock:
                done += 1
                self.per_source.setdefault(source, new_src_stats())["articles"] += n
                self.articles_downloaded += n

        with ThreadPoolExecutor(max_workers=self.cfg.workers) as pool:
            for _ in as_completed([pool.submit(work, it) for it in rows]):
                pass

        self.stats["failed"] = self.failed
        self._report()
        self.db.close()
        return 1 if self.failed else 0

    # ---- post-sync summary --------------------------------------------- #

    def _report(self) -> None:
        finished = utcnow()
        mirror = self.db.summary_by_source()
        self.write_json_manifest(finished, mirror)
        self.ui.summary(
            self.started_at, finished, self.stats, self.per_source, mirror,
            self.source_urls, self.bytes_downloaded, self.articles_downloaded,
        )

    def write_json_manifest(self, finished: str, mirror: dict) -> None:
        sources = sorted(set(mirror) | set(self.per_source))
        new_dl = self.stats["verified"] + self.stats["downloaded"]
        manifest = {
            "started_at": self.started_at,
            "finished_at": finished,
            "this_run": {
                "newly_downloaded": new_dl,
                "already_current": self.stats["skipped"],
                "failed": self.stats["failed"],
                "bytes_downloaded": self.bytes_downloaded,
                "articles_added": self.articles_downloaded,
            },
            "sources": {
                source: {
                    "url": self.source_urls.get(source),
                    "mirror_files": mirror.get(source, {}).get("files", 0),
                    "mirror_bytes": mirror.get(source, {}).get("bytes", 0),
                    "mirror_articles": mirror.get(source, {}).get("articles", 0),
                    "files_counted": mirror.get(source, {}).get("counted", 0),
                    **self.per_source.get(source, new_src_stats()),
                }
                for source in sources
            },
            "mirror_total": {
                "files": sum(m.get("files", 0) for m in mirror.values()),
                "bytes": sum(m.get("bytes", 0) for m in mirror.values()),
                "articles": sum(m.get("articles", 0) for m in mirror.values()),
            },
        }
        out = self.cfg.log_dir / f"summary_{dt.date.today().isoformat()}.json"
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(manifest, indent=2))
            LOG.info("wrote summary manifest: %s", out)
        except OSError as exc:
            LOG.warning("could not write summary manifest: %s", exc)


def md5_file_from_url(text: str) -> str:
    import re
    m = re.search(r"=\s*([0-9a-fA-F]{32})", text)
    if m:
        return m.group(1)
    m = re.search(r"\b([0-9a-fA-F]{32})\b", text)
    if not m:
        raise ValueError(f"could not parse md5 from: {text[:120]!r}")
    return m.group(1)
