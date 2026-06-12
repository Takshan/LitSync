from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional


DEFAULT_WORKERS = 4


@dataclasses.dataclass
class Config:
    data_root: Path
    email: str
    sources: tuple[str, ...]
    pmc_groups: tuple[str, ...]
    pmc_formats: tuple[str, ...]
    workers: int = DEFAULT_WORKERS
    max_retries: int = 5
    backoff_base: float = 2.0
    timeout: int = 60
    dry_run: bool = False
    reverify: bool = False
    prune: bool = False
    count_articles: bool = False
    fda_endpoints: Optional[tuple[str, ...]] = None

    @property
    def state_dir(self) -> Path:
        return self.data_root / "_state"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.sqlite"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "litsync.lock"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"
