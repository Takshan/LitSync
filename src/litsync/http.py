from __future__ import annotations

import logging
import os
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional

import requests
from requests.adapters import HTTPAdapter

from litsync import __version__
from litsync.config import Config

LOG = logging.getLogger("litsync")
CHUNK = 1 << 20


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.hrefs.append(v)


class HttpClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=cfg.workers * 2, pool_maxsize=cfg.workers * 2)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "User-Agent": f"litsync/{__version__} (mailto:{cfg.email}) python-requests",
                "Accept-Encoding": "identity",
            }
        )

    def _retry(self, fn, what: str):
        last = None
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                return fn()
            except (requests.RequestException, OSError) as exc:
                last = exc
                wait = self.cfg.backoff_base ** attempt
                LOG.warning("attempt %d/%d failed for %s: %s (retry in %.0fs)",
                            attempt, self.cfg.max_retries, what, exc, wait)
                time.sleep(wait)
        raise last

    def list_dir(self, url: str) -> list[str]:
        def _do():
            r = self.session.get(url, timeout=self.cfg.timeout)
            r.raise_for_status()
            return r.text
        html = self._retry(_do, f"list {url}")
        parser = _LinkParser()
        parser.feed(html)
        names = []
        for href in parser.hrefs:
            if href.startswith("?") or href.startswith("/") or href.startswith(".."):
                continue
            href = href.split("?")[0].split("#")[0]
            if not href or href.endswith("/"):
                continue
            names.append(href)
        return names

    def get_text(self, url: str) -> str:
        def _do():
            r = self.session.get(url, timeout=self.cfg.timeout)
            r.raise_for_status()
            return r.text
        return self._retry(_do, f"get {url}")

    def head(self, url: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
        def _do():
            r = self.session.head(url, timeout=self.cfg.timeout, allow_redirects=True)
            r.raise_for_status()
            return r
        r = self._retry(_do, f"head {url}")
        size = int(r.headers["Content-Length"]) if "Content-Length" in r.headers else None
        return size, r.headers.get("Last-Modified"), r.headers.get("ETag")

    def download(
        self,
        url: str,
        dest: Path,
        expected_size: Optional[int],
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        existing = part.stat().st_size if part.exists() else 0

        def _do():
            headers = {}
            mode = "wb"
            if existing and expected_size and existing < expected_size:
                headers["Range"] = f"bytes={existing}-"
                mode = "ab"
            with self.session.get(url, stream=True, timeout=self.cfg.timeout,
                                  headers=headers) as r:
                if "Range" in headers and r.status_code == 200:
                    mode = "wb"
                r.raise_for_status()
                with open(part, mode) as fh:
                    for chunk in r.iter_content(CHUNK):
                        if chunk:
                            fh.write(chunk)
                            if progress_callback:
                                progress_callback(len(chunk))
            return part.stat().st_size

        written = self._retry(_do, f"download {url}")
        if expected_size is not None and written != expected_size:
            part.unlink(missing_ok=True)
            raise IOError(f"size mismatch for {url}: got {written}, expected {expected_size}")
        os.replace(part, dest)
        return written
