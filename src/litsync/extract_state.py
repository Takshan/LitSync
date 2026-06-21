from __future__ import annotations

import datetime
import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("litsync")


class ExtractState:
    """SQLite-backed resume state for litsync-extract.

    Tracks which source files have already been extracted so re-runs can skip
    them. For yearly corpora it also tracks the last shard index written per
    year, so re-runs can continue without overwriting completed shards.

    If the output directory, source list, or shard size changes, the stored
    state is reset automatically.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS extracted_files (
        source_file TEXT PRIMARY KEY,
        records INTEGER NOT NULL,
        extracted_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS extracted_shards (
        source_file TEXT NOT NULL,
        year TEXT NOT NULL,
        end_shard INTEGER NOT NULL,
        PRIMARY KEY (source_file, year)
    );

    CREATE INDEX IF NOT EXISTS idx_extracted_shards_year
        ON extracted_shards(year, end_shard);

    CREATE INDEX IF NOT EXISTS idx_extracted_shards_file
        ON extracted_shards(source_file);
    """

    def __init__(
        self,
        state_path: Path,
        out_dir: Path,
        sources: list[str],
        shard_size_mb: int,
        reset: bool = False,
    ):
        self.state_path = state_path
        self.out_dir = out_dir
        self.sources = sources
        self.shard_size_mb = shard_size_mb

        state_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(state_path))
        self._conn.executescript(self._SCHEMA)
        self._migrate_old_schema()

        if reset or not self._matches_meta():
            if reset:
                LOG.info("resetting extraction state")
            else:
                LOG.info("extraction config changed; resetting state")
            self._reset_tables()
            self._write_meta()

    def _migrate_old_schema(self) -> None:
        """Migrate pre-yearly schema that had a single end_shard column."""
        cursor = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='extracted_files'"
        )
        row = cursor.fetchone()
        if not row or "end_shard" not in (row[0] or "").lower():
            return
        LOG.info("migrating old extract_state schema")
        self._conn.execute("BEGIN")
        self._conn.execute(
            """
            CREATE TABLE extracted_files_new (
                source_file TEXT PRIMARY KEY,
                records INTEGER NOT NULL,
                extracted_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            INSERT INTO extracted_files_new (source_file, records, extracted_at)
            SELECT source_file, records, extracted_at FROM extracted_files
            """
        )
        self._conn.execute("DROP TABLE extracted_files")
        self._conn.execute(
            "ALTER TABLE extracted_files_new RENAME TO extracted_files"
        )
        self._conn.commit()

    def _matches_meta(self) -> bool:
        try:
            stored = dict(
                self._conn.execute("SELECT key, value FROM meta").fetchall()
            )
        except sqlite3.OperationalError:
            return False
        return (
            stored.get("out_dir") == str(self.out_dir)
            and stored.get("sources") == json.dumps(self.sources)
            and stored.get("shard_size_mb") == str(self.shard_size_mb)
        )

    def _write_meta(self) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            [
                ("out_dir", str(self.out_dir)),
                ("sources", json.dumps(self.sources)),
                ("shard_size_mb", str(self.shard_size_mb)),
            ],
        )
        self._conn.commit()

    def _reset_tables(self) -> None:
        self._conn.execute("DELETE FROM meta")
        self._conn.execute("DELETE FROM extracted_files")
        self._conn.execute("DELETE FROM extracted_shards")
        self._conn.commit()

    def is_done(self, source_file: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM extracted_files WHERE source_file = ?",
            (source_file,),
        ).fetchone()
        return row is not None

    def mark_done(self, source_file: str, records: int,
                  year_shards: dict[str, int]) -> None:
        now = datetime.datetime.now().isoformat(timespec="seconds")
        self._conn.execute("BEGIN")
        self._conn.execute(
            "INSERT OR REPLACE INTO extracted_files "
            "(source_file, records, extracted_at) VALUES (?, ?, ?)",
            (source_file, records, now),
        )
        self._conn.execute(
            "DELETE FROM extracted_shards WHERE source_file = ?",
            (source_file,),
        )
        self._conn.executemany(
            "INSERT INTO extracted_shards (source_file, year, end_shard) VALUES (?, ?, ?)",
            [(source_file, year, end_shard) for year, end_shard in year_shards.items()],
        )
        self._conn.commit()

    def done_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM extracted_files"
        ).fetchone()
        return row[0] if row else 0

    def max_end_shard(self, year: str = "") -> int:
        """Highest completed shard index for a given year (empty = non-yearly)."""
        row = self._conn.execute(
            "SELECT MAX(end_shard) FROM extracted_shards WHERE year = ?",
            (year,),
        ).fetchone()
        return row[0] if row and row[0] is not None else 0

    def year_shards_for_file(self, source_file: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT year, end_shard FROM extracted_shards WHERE source_file = ?",
            (source_file,),
        ).fetchall()
        return dict(rows)

    def close(self) -> None:
        self._conn.close()
