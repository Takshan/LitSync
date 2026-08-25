from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import gzip
import hashlib
import logging
import os
import re
import tarfile
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("litsync")
CHUNK = 1 << 20  # 1 MiB


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def new_src_stats() -> dict:
    return {"new": 0, "existing": 0, "skipped": 0,
            "downloaded": 0, "verified": 0, "failed": 0, "bytes": 0, "articles": 0}


def human_bytes(n) -> str:
    size = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def partial_note(counted: int, files: int) -> str:
    return "" if counted >= files else f"  [partial: {counted}/{files} files counted]"


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_md5(text: str) -> str:
    """NCBI .md5 sidecars look like 'MD5(file.xml.gz)= <hex>' or '<hex>  file'."""
    m = re.search(r"=\s*([0-9a-fA-F]{32})", text)
    if m:
        return m.group(1)
    m = re.search(r"\b([0-9a-fA-F]{32})\b", text)
    if not m:
        raise ValueError(f"could not parse md5 from: {text[:120]!r}")
    return m.group(1)


@contextlib.contextmanager
def run_lock(path: Path):
    """Prevent overlapping runs via an exclusive file lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another litsync run is already in progress; exiting")
        fh.write(f"{os.getpid()} {utcnow()}\n")
        fh.flush()
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def count_articles(path: Path, source: str) -> int:
    """Cheaply count the articles/records inside one downloaded file."""
    name = path.name.lower()
    if source.startswith("pubmed"):
        if not name.endswith(".xml.gz"):
            return 0
        needle = b"<PubmedArticle>"
        overlap = b""
        count = 0
        with gzip.open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK), b""):
                buf = overlap + chunk
                count += buf.count(needle)
                overlap = buf[-(len(needle) - 1):]
        return count
    if source.startswith("pmc"):
        if name.endswith(".xml"):
            return 1
        if not name.endswith(".tar.gz"):
            return 0
        count = 0
        with tarfile.open(path, mode="r|gz") as tar:
            for member in tar:
                low = member.name.lower()
                if member.isfile() and (low.endswith(".xml") or low.endswith(".nxml")):
                    count += 1
        return count
    if source.startswith("fda"):
        if not name.endswith(".zip"):
            return 0
        import json
        extract_dir = path.parent / name[:-9] if name.endswith(".json.zip") else path.with_suffix("")
        count = 0
        for json_file in extract_dir.rglob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                count += len(data.get("results", []))
            except Exception:
                pass
        return count
    if source.startswith("clinicaltrials"):
        if not name.endswith(".zip"):
            return 0
        extract_dir = path.with_suffix("")
        return sum(1 for p in extract_dir.rglob("*.xml") if p.is_file())
    return 0
