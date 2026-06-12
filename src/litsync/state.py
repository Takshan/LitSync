from __future__ import annotations

import contextlib
import dataclasses
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from litsync.utils import utcnow


@dataclasses.dataclass
class FileRecord:
    source: str
    filename: str
    url: str
    rel_path: str
    remote_size: Optional[int] = None
    remote_mtime: Optional[str] = None
    etag: Optional[str] = None
    md5: Optional[str] = None
    local_md5: Optional[str] = None
    status: str = "pending"
    attempts: int = 0
    error: Optional[str] = None


class StateDB:
    """Thread-safe-enough SQLite wrapper (single connection guarded by a lock)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    source        TEXT NOT NULL,
                    filename      TEXT NOT NULL,
                    url           TEXT NOT NULL,
                    rel_path      TEXT NOT NULL,
                    remote_size   INTEGER,
                    remote_mtime  TEXT,
                    etag          TEXT,
                    md5           TEXT,
                    local_md5     TEXT,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    attempts      INTEGER NOT NULL DEFAULT 0,
                    error         TEXT,
                    article_count INTEGER,
                    first_seen    TEXT NOT NULL,
                    last_checked  TEXT NOT NULL,
                    completed_at  TEXT,
                    PRIMARY KEY (source, filename)
                )
                """
            )
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute("ALTER TABLE files ADD COLUMN article_count INTEGER")
            self._conn.commit()

    def get(self, source: str, filename: str) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM files WHERE source=? AND filename=?", (source, filename)
            )
            return cur.fetchone()

    def all_sources(self) -> set[str]:
        with self._lock:
            cur = self._conn.execute("SELECT DISTINCT source FROM files")
            return {r["source"] for r in cur.fetchall()}

    def known_filenames(self, source: str) -> set[str]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT filename FROM files WHERE source=?", (source,)
            )
            return {r["filename"] for r in cur.fetchall()}

    def upsert_seen(self, rec: FileRecord) -> None:
        now = utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO files (source, filename, url, rel_path, first_seen, last_checked)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(source, filename) DO UPDATE SET
                    url=excluded.url, rel_path=excluded.rel_path, last_checked=excluded.last_checked
                """,
                (rec.source, rec.filename, rec.url, rec.rel_path, now, now),
            )
            self._conn.commit()

    def mark(self, source: str, filename: str, **fields) -> None:
        if not fields:
            return
        fields["last_checked"] = utcnow()
        if fields.get("status") in ("done", "verified"):
            fields["completed_at"] = utcnow()
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [source, filename]
        with self._lock:
            self._conn.execute(
                f"UPDATE files SET {cols} WHERE source=? AND filename=?", vals
            )
            self._conn.commit()

    def summary(self) -> dict[str, int]:
        with self._lock:
            cur = self._conn.execute("SELECT status, COUNT(*) c FROM files GROUP BY status")
            return {r["status"]: r["c"] for r in cur.fetchall()}

    def summary_by_source(self) -> dict[str, dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT source, COUNT(*) c, COALESCE(SUM(remote_size),0) bytes, "
                "COALESCE(SUM(article_count),0) articles, "
                "SUM(article_count IS NOT NULL) counted, "
                "SUM(status='verified') verified, SUM(status='failed') failed "
                "FROM files GROUP BY source"
            )
            return {
                r["source"]: {
                    "files": r["c"], "bytes": r["bytes"],
                    "articles": r["articles"], "counted": r["counted"] or 0,
                    "verified": r["verified"] or 0, "failed": r["failed"] or 0,
                }
                for r in cur.fetchall()
            }

    def files_missing_counts(self) -> list[tuple[str, str, str]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT source, filename, rel_path FROM files "
                "WHERE article_count IS NULL AND status='verified'"
            )
            return [(r["source"], r["filename"], r["rel_path"]) for r in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
