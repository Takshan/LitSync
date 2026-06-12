from __future__ import annotations

from litsync.config import Config
from litsync.http import HttpClient
from litsync.sources import Task

PUBMED_BASE = "https://ftp.ncbi.nlm.nih.gov/pubmed"


class PubMedSource:
    """PubMed baseline + daily update files. Files are immutable and have .md5 sidecars."""

    SUFFIX = ".xml.gz"

    def __init__(self, cfg: Config, http: HttpClient):
        self.cfg, self.http = cfg, http

    def _plan_dir(self, subdir: str) -> list[Task]:
        source = f"pubmed_{subdir}"
        url = f"{PUBMED_BASE}/{subdir}/"
        names = [n for n in self.http.list_dir(url) if n.endswith(self.SUFFIX)]
        tasks = []
        for name in sorted(names):
            rel = f"pubmed/{subdir}/{name}"
            tasks.append(Task(
                source=source,
                filename=name,
                url=url + name,
                dest=self.cfg.data_root / rel,
                rel_path=rel,
                md5_url=f"{url}{name}.md5",
                immutable=True,
            ))
        return tasks

    def plan(self) -> list[Task]:
        return self._plan_dir("baseline") + self._plan_dir("updatefiles")
