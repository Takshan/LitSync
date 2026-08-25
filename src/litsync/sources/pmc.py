from __future__ import annotations

import csv
import gzip
import json
import logging
import re
import xml.etree.ElementTree as ET

from litsync.config import Config
from litsync.http import HttpClient
from litsync.sources import Task
from litsync.state import StateDB

LOG = logging.getLogger("litsync")

PMC_BASE = "https://pmc-oa-opendata.s3.amazonaws.com"
INVENTORY_PREFIX = "inventory-reports/pmc-oa-opendata/metadata/"
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}Z/$")
ARTICLE_KEY_RE = re.compile(r"^metadata/(PMC\d+\.\d+)\.json$")


class PmcSource:
    """PMC Article Datasets on AWS (pmc-oa-opendata bucket, anonymous HTTPS).

    The bucket has no bulk packages; each article version lives under its own
    prefix (PMCnnnnnnnn.v/) with predictable object names. The daily S3
    inventory (one row per metadata JSON, with ETag) serves as the diff:
    only new or changed article versions produce download tasks.

    PDFs and media files are not mirrored: they exist only for some articles
    and are enumerable only via each article's JSON metadata.
    """

    # plan() emits only changed articles, never the full remote listing,
    # so Syncer must not prune these sources.
    full_enumeration = False

    def __init__(self, cfg: Config, http: HttpClient):
        self.cfg, self.http = cfg, http
        self.source_names = ("pmc_inventory", "pmc_metadata") + tuple(
            f"pmc_{fmt}" for fmt in cfg.pmc_formats
        )

    def _latest_snapshot(self) -> str:
        url = f"{PMC_BASE}/?list-type=2&prefix={INVENTORY_PREFIX}&delimiter=/"
        root = ET.fromstring(self.http.get_text(url))
        snapshots = sorted(
            p for el in root.iter(f"{S3_NS}Prefix")
            if el.text and SNAPSHOT_RE.match(p := el.text.removeprefix(INVENTORY_PREFIX))
        )
        if not snapshots:
            raise RuntimeError(f"no inventory snapshots found under {INVENTORY_PREFIX}")
        return snapshots[-1]

    def _inventory_keys(self, snapshot: str) -> list[str]:
        url = f"{PMC_BASE}/{INVENTORY_PREFIX}{snapshot}manifest.json"
        manifest = json.loads(self.http.get_text(url))
        return [f["key"] for f in manifest["files"]]

    def _read_rows(self, key: str) -> list[tuple[str, str]]:
        url = f"{PMC_BASE}/{key}"

        def _do():
            rows = []
            with self.http.session.get(url, stream=True, timeout=self.cfg.timeout) as r:
                r.raise_for_status()
                with gzip.open(r.raw, "rt", encoding="utf-8") as fh:
                    for fields in csv.reader(fh):
                        if len(fields) >= 4:
                            rows.append((fields[1], fields[3]))
            return rows

        return self.http._retry(_do, f"inventory {key}")

    def _is_current(self, db: StateDB, article: str, etag: str) -> bool:
        row = db.get("pmc_metadata", f"{article}.json")
        if (row is None or row["status"] != "verified"
                or (row["etag"] or "").strip('"') != etag.strip('"')):
            return False
        for source, fname in [("pmc_metadata", f"{article}.json")] + [
                (f"pmc_{fmt}", f"{article}.{fmt}") for fmt in self.cfg.pmc_formats]:
            row = db.get(source, fname)
            if row is None or row["status"] != "verified":
                return False
            if not (self.cfg.data_root / row["rel_path"]).exists():
                return False
        return True

    def _article_tasks(self, article: str) -> list[Task]:
        tasks = [Task(
            source="pmc_metadata",
            filename=f"{article}.json",
            url=f"{PMC_BASE}/metadata/{article}.json",
            dest=self.cfg.data_root / "pmc" / "articles" / article / f"{article}.json",
            rel_path=f"pmc/articles/{article}/{article}.json",
            md5_url=None,
            immutable=False,
        )]
        for fmt in self.cfg.pmc_formats:
            rel = f"pmc/articles/{article}/{article}.{fmt}"
            tasks.append(Task(
                source=f"pmc_{fmt}",
                filename=f"{article}.{fmt}",
                url=f"{PMC_BASE}/{article}/{article}.{fmt}",
                dest=self.cfg.data_root / rel,
                rel_path=rel,
                md5_url=None,
                immutable=False,
            ))
        return tasks

    def plan(self) -> list[Task]:
        snapshot = self._latest_snapshot()
        LOG.info("pmc inventory snapshot: %s", snapshot)
        keys = self._inventory_keys(snapshot)

        tasks: list[Task] = []
        for key in keys:
            name = key.rsplit("/", 1)[-1]
            rel = f"pmc/inventory/{snapshot}{name}"
            tasks.append(Task(
                source="pmc_inventory",
                filename=name,
                url=f"{PMC_BASE}/{key}",
                dest=self.cfg.data_root / rel,
                rel_path=rel,
                md5_url=None,
                immutable=True,
            ))

        db = StateDB(self.cfg.db_path)
        current = changed = 0
        try:
            for key in keys:
                for article_key, etag in self._read_rows(key):
                    m = ARTICLE_KEY_RE.match(article_key)
                    if not m:
                        continue
                    if self._is_current(db, m.group(1), etag):
                        current += 1
                        continue
                    tasks.extend(self._article_tasks(m.group(1)))
                    changed += 1
        finally:
            db.close()
        LOG.info("pmc: %d article versions current, %d to sync", current, changed)
        return tasks
