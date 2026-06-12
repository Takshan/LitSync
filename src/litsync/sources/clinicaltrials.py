from __future__ import annotations

from litsync.config import Config
from litsync.http import HttpClient
from litsync.sources import Task

CLINICALTRIALS_XML_URL = "https://clinicaltrials.gov/api/legacy/public-xml?format=zip"


class ClinicalTrialsSource:
    """ClinicalTrials.gov full public XML dump."""

    def __init__(self, cfg: Config, http: HttpClient):
        self.cfg, self.http = cfg, http

    def plan(self) -> list[Task]:
        name = "ctg-public-xml.zip"
        rel = f"clinicaltrials/{name}"
        return [Task(
            source="clinicaltrials_xml",
            filename=name,
            url=CLINICALTRIALS_XML_URL,
            dest=self.cfg.data_root / rel,
            rel_path=rel,
            md5_url=None,
            immutable=True,
            extract=True,
        )]
