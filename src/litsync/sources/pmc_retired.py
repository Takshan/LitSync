from __future__ import annotations

from litsync.config import Config
from litsync.http import HttpClient
from litsync.sources import Task

PMC_BASE = "https://ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated"


class PmcSource:
    """PMC OA bulk: baseline + daily incremental .tar.gz per group/format."""

    def __init__(self, cfg: Config, http: HttpClient):
        self.cfg, self.http = cfg, http

    def _plan_bulk_dir(self, group: str, fmt: str) -> list[Task]:
        source = f"pmc_{group}_{fmt}"
        url = f"{PMC_BASE}/oa_bulk/{group}/{fmt}/"
        names = self.http.list_dir(url)
        tasks = []
        for name in sorted(names):
            if not (name.endswith(".tar.gz") or name.endswith(".filelist.csv")
                    or name.endswith(".filelist.txt")):
                continue
            rel = f"pmc/oa_bulk/{group}/{fmt}/{name}"
            tasks.append(Task(
                source=source,
                filename=name,
                url=url + name,
                dest=self.cfg.data_root / rel,
                rel_path=rel,
                md5_url=None,
                immutable=True,
            ))
        return tasks

    def plan(self) -> list[Task]:
        tasks: list[Task] = []
        for group in self.cfg.pmc_groups:
            for fmt in self.cfg.pmc_formats:
                tasks.extend(self._plan_bulk_dir(group, fmt))
        tasks.append(Task(
            source="pmc_idmap",
            filename="oa_file_list.csv",
            url=f"{PMC_BASE}/oa_file_list.csv",
            dest=self.cfg.data_root / "pmc" / "oa_file_list.csv",
            rel_path="pmc/oa_file_list.csv",
            md5_url=None,
            immutable=False,
        ))
        return tasks
