from __future__ import annotations

import json

from litsync.config import Config
from litsync.http import HttpClient
from litsync.sources import Task

FDA_MANIFEST_URL = "https://api.fda.gov/download.json"


class FdaSource:
    """openFDA bulk snapshots from the download manifest."""

    def __init__(self, cfg: Config, http: HttpClient):
        self.cfg, self.http = cfg, http

    def _should_include(self, category: str, endpoint: str) -> bool:
        if not self.cfg.fda_endpoints:
            return True
        return f"{category}/{endpoint}" in self.cfg.fda_endpoints

    def plan(self) -> list[Task]:
        text = self.http.get_text(FDA_MANIFEST_URL)
        manifest = json.loads(text)
        tasks: list[Task] = []
        for category, endpoints in manifest.get("results", {}).items():
            for endpoint, info in endpoints.items():
                if not self._should_include(category, endpoint):
                    continue
                source = f"fda_{category}_{endpoint}"
                for part in info.get("partitions", []):
                    url = part.get("file")
                    if not url:
                        continue
                    name = url.rsplit("/", 1)[-1]
                    rel = f"fda/{category}/{endpoint}/{name}"
                    tasks.append(Task(
                        source=source,
                        filename=name,
                        url=url,
                        dest=self.cfg.data_root / rel,
                        rel_path=rel,
                        md5_url=None,
                        immutable=True,
                        extract=True,
                    ))
        return tasks
