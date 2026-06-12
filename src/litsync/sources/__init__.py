from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional


@dataclasses.dataclass
class Task:
    source: str
    filename: str
    url: str
    dest: Path
    rel_path: str
    md5_url: Optional[str] = None
    immutable: bool = True
    extract: bool = False
