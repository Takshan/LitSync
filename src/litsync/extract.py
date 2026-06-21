from __future__ import annotations

import gzip
import io
import json
import logging
import re
import tarfile
import time
from pathlib import Path
from typing import Iterator, Optional
from xml.etree import ElementTree as ET

from litsync.extract_state import ExtractState
from litsync.ui import UI

LOG = logging.getLogger("litsync")
_WS = re.compile(r"\s+")


def clean(s: Optional[str]) -> str:
    return _WS.sub(" ", s).strip() if s else ""


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return clean(" ".join(el.itertext()))


def find_local(parent: ET.Element, name: str) -> Optional[ET.Element]:
    for el in parent.iter():
        if local_tag(el.tag) == name:
            return el
    return None


def findall_local(parent: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in parent.iter() if local_tag(el.tag) == name]


# --------------------------------------------------------------------------- #
# PubMed
# --------------------------------------------------------------------------- #
def pubmed_year(article: ET.Element) -> Optional[int]:
    for pd in findall_local(article, "PubDate"):
        y = pd.find("Year")
        if y is not None and y.text and y.text.strip().isdigit():
            return int(y.text.strip())
        md = pd.find("MedlineDate")
        if md is not None and md.text:
            m = re.search(r"\d{4}", md.text)
            if m:
                return int(m.group())
    return None


def parse_pubmed(raw: bytes, source_file: str) -> Iterator[dict]:
    for _, elem in ET.iterparse(io.BytesIO(raw), events=("end",)):
        if local_tag(elem.tag) != "PubmedArticle":
            continue
        try:
            pmid_el = find_local(elem, "PMID")
            title = text(find_local(elem, "ArticleTitle"))
            abstract = " ".join(text(a) for a in findall_local(elem, "AbstractText")).strip()
            journal = ""
            jt = find_local(elem, "Journal")
            if jt is not None:
                journal = text(jt.find("Title"))
            authors = []
            for au in findall_local(elem, "Author"):
                last = au.findtext("LastName") or ""
                init = au.findtext("Initials") or ""
                coll = au.findtext("CollectiveName") or ""
                name = clean(f"{last} {init}".strip() or coll)
                if name:
                    authors.append(name)
            mesh = [text(d) for d in findall_local(elem, "DescriptorName") if text(d)]
            keywords = [text(k) for k in findall_local(elem, "Keyword") if text(k)]
            doi = None
            for aid in findall_local(elem, "ArticleId"):
                if aid.get("IdType") == "doi" and aid.text:
                    doi = aid.text.strip()
                    break
            yield {
                "source": "pubmed",
                "pmid": pmid_el.text.strip() if pmid_el is not None and pmid_el.text else None,
                "pmcid": None,
                "doi": doi,
                "title": title,
                "abstract": clean(abstract),
                "body": "",
                "journal": journal,
                "year": pubmed_year(elem),
                "authors": authors,
                "mesh": mesh,
                "keywords": keywords,
                "source_file": source_file,
            }
        finally:
            elem.clear()


# --------------------------------------------------------------------------- #
# PMC
# --------------------------------------------------------------------------- #
def parse_pmc_article(raw: bytes, source_file: str) -> Optional[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None

    ids = {}
    for aid in findall_local(root, "article-id"):
        t = aid.get("pub-id-type") or aid.get("{http://www.w3.org/1999/xlink}type")
        if t and aid.text:
            ids[t] = aid.text.strip()

    pmcid = ids.get("pmc") or ids.get("pmcid")
    if pmcid and not pmcid.upper().startswith("PMC"):
        pmcid = "PMC" + pmcid

    title = text(find_local(root, "article-title"))
    abstract = " ".join(text(a) for a in findall_local(root, "abstract")).strip()
    journal = text(find_local(root, "journal-title"))

    year = None
    for pd in findall_local(root, "pub-date"):
        y = pd.find("year")
        if y is not None and y.text and y.text.strip().isdigit():
            year = int(y.text.strip())
            break

    authors = []
    for contrib in findall_local(root, "contrib"):
        if contrib.get("contrib-type") not in (None, "author"):
            continue
        name = find_local(contrib, "name")
        if name is not None:
            sur = name.findtext("surname") or ""
            giv = name.findtext("given-names") or ""
            full = clean(f"{sur} {giv}".strip())
            if full:
                authors.append(full)

    body_el = find_local(root, "body")
    body = text(body_el)

    return {
        "source": "pmc",
        "pmid": ids.get("pmid"),
        "pmcid": pmcid,
        "doi": ids.get("doi"),
        "title": title,
        "abstract": clean(abstract),
        "body": body,
        "journal": journal,
        "year": year,
        "authors": authors,
        "mesh": [],
        "keywords": [text(k) for k in findall_local(root, "kwd") if text(k)],
        "source_file": source_file,
    }


def parse_pmc_tar(path: Path, rel: str) -> Iterator[dict]:
    with tarfile.open(path, mode="r|gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            low = member.name.lower()
            if not (low.endswith(".xml") or low.endswith(".nxml")):
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            rec = parse_pmc_article(f.read(), f"{rel}::{member.name}")
            if rec is not None:
                yield rec


# --------------------------------------------------------------------------- #
# openFDA
# --------------------------------------------------------------------------- #
def _flatten_fda_value(v):
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return " ".join(_flatten_fda_value(x) for x in v)
    if isinstance(v, dict):
        return " ".join(_flatten_fda_value(x) for x in v.values())
    return ""


def parse_fda_json_file(path: Path, rel: str) -> Iterator[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results", [])
    parts = Path(rel).parts
    endpoint = "/".join(parts[1:3]) if len(parts) >= 3 else ""
    meta = data.get("meta", {})
    last_updated = meta.get("last_updated")
    for rec in results:
        text = clean(_flatten_fda_value(rec))
        rid = rec.get("safetyreportid") or rec.get("set_id") or rec.get("id")
        if not rid and isinstance(rec, dict):
            for v in rec.values():
                if isinstance(v, str):
                    rid = v
                    break
        yield {
            "source": "fda",
            "fda_endpoint": endpoint,
            "id": str(rid) if rid else None,
            "title": "",
            "abstract": "",
            "body": text,
            "journal": "",
            "year": None,
            "authors": [],
            "mesh": [],
            "keywords": [],
            "source_file": rel,
            "last_updated": last_updated,
        }


# --------------------------------------------------------------------------- #
# ClinicalTrials
# --------------------------------------------------------------------------- #
def parse_clinicaltrials_xml_file(path: Path, rel: str) -> Optional[dict]:
    try:
        root = ET.fromstring(path.read_bytes())
    except ET.ParseError:
        return None

    def find(tag: str) -> Optional[ET.Element]:
        return find_local(root, tag)

    def findall(tag: str) -> list[ET.Element]:
        return findall_local(root, tag)

    nct_id = text(find("nct_id")) or text(find("nctId"))
    title = text(find("official_title")) or text(find("brief_title"))
    brief_summary = text(find("brief_summary"))
    detailed_description = text(find("detailed_description"))
    eligibility = text(find("eligibility"))
    conditions = [text(c) for c in findall("condition")]
    interventions = [text(i) for i in findall("intervention")]
    phases = [text(p) for p in findall("phase")]
    statuses = [text(s) for s in findall("overall_status")]

    year = None
    for dtag in ("start_date", "completion_date", "verification_date", "study_first_submitted"):
        d = text(find(dtag))
        if d:
            m = re.search(r"\d{4}", d)
            if m:
                year = int(m.group())
                break

    body = "\n\n".join(filter(None, [
        brief_summary,
        detailed_description,
        eligibility,
        "Conditions: " + ", ".join(conditions) if conditions else "",
        "Interventions: " + ", ".join(interventions) if interventions else "",
    ]))

    return {
        "source": "clinicaltrials",
        "nct_id": nct_id,
        "pmid": None,
        "pmcid": None,
        "doi": None,
        "title": title,
        "abstract": clean(brief_summary),
        "body": clean(body),
        "journal": "",
        "year": year,
        "authors": [],
        "mesh": [],
        "keywords": conditions + interventions,
        "source_file": rel,
        "phase": phases[0] if phases else None,
        "overall_status": statuses[0] if statuses else None,
    }


# --------------------------------------------------------------------------- #
# Sharded JSONL writer
# --------------------------------------------------------------------------- #
class ShardWriter:
    def __init__(self, out_dir: Path, shard_bytes: int, prefix: str = "corpus",
                 start_idx: int = 0):
        self.out_dir = out_dir
        self.shard_bytes = shard_bytes
        self.prefix = prefix
        self.idx = start_idx
        self.bytes = 0
        self.records = 0
        self.fh = None

    @property
    def current_shard(self) -> int:
        return self.idx

    def _roll(self):
        if self.fh:
            self.fh.close()
        self.idx += 1
        self.bytes = 0
        name = f"{self.prefix}-{self.idx:05d}.jsonl"
        self.fh = open(self.out_dir / name, "w", encoding="utf-8")
        LOG.info("writing shard %s", name)

    def write(self, rec: dict):
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        data = line.encode("utf-8")
        if self.fh is None or (
            self.bytes and self.bytes + len(data) > self.shard_bytes
        ):
            self._roll()
        self.fh.write(line)
        self.bytes += len(data)
        self.records += 1

    def close(self):
        if self.fh:
            self.fh.close()

    def roll(self):
        """Close the current shard so the next write starts a fresh shard."""
        if self.fh:
            self.fh.close()
            self.fh = None
        self.bytes = 0


class _ShardWriters:
    """Manages one ShardWriter per year (or a single writer for non-yearly)."""

    def __init__(self, out_dir: Path, shard_bytes: int, yearly: bool,
                 state: ExtractState, resume: bool):
        self.out_dir = out_dir
        self.shard_bytes = shard_bytes
        self.yearly = yearly
        self.state = state
        self.resume = resume
        self.writers: dict[str, ShardWriter] = {}
        self.records = 0

    def _year_key(self, rec: dict) -> str:
        if not self.yearly:
            return ""
        year = rec.get("year")
        return str(year) if year is not None else "unknown"

    def _start_idx(self, year: str) -> int:
        if not self.resume:
            return 0
        return self.state.max_end_shard(year)

    def _cleanup_partial_shards(self, year: str, year_dir: Path) -> None:
        resume_idx = self._start_idx(year)
        for shard_path in sorted(year_dir.glob("corpus-*.jsonl")):
            try:
                idx = int(shard_path.stem.rsplit("-", 1)[-1])
            except ValueError:
                continue
            if idx > resume_idx:
                LOG.info("removing partial shard %s", shard_path.relative_to(self.out_dir))
                shard_path.unlink()

    @staticmethod
    def _count_existing_records(year_dir: Path, max_idx: int) -> int:
        count = 0
        for idx in range(1, max_idx + 1):
            shard_path = year_dir / f"corpus-{idx:05d}.jsonl"
            if not shard_path.exists():
                continue
            with open(shard_path, "r", encoding="utf-8") as fh:
                for _ in fh:
                    count += 1
        return count

    def write(self, rec: dict, touched: Optional[set[str]] = None):
        year = self._year_key(rec)
        writer = self.writers.get(year)
        if writer is None:
            year_dir = self.out_dir / year if self.yearly else self.out_dir
            year_dir.mkdir(parents=True, exist_ok=True)
            self._cleanup_partial_shards(year, year_dir)
            start_idx = self._start_idx(year)
            writer = ShardWriter(
                year_dir,
                self.shard_bytes,
                start_idx=start_idx,
            )
            writer.records = self._count_existing_records(year_dir, start_idx)
            self.records += writer.records
            self.writers[year] = writer
        writer.write(rec)
        self.records += 1
        if touched is not None:
            touched.add(year)

    def current_shards(self) -> dict[str, int]:
        return {year: writer.current_shard for year, writer in self.writers.items()}

    def close(self):
        for writer in self.writers.values():
            writer.close()


def iter_source_files(data_root: Path, sources: list[str], limit: Optional[int]):
    if "pubmed" in sources:
        files = sorted(data_root.glob("pubmed/**/*.xml.gz"))
        for p in files[: limit if limit else None]:
            yield "pubmed", p
    if "pmc" in sources:
        files = sorted(data_root.glob("pmc/**/*.tar.gz"))
        for p in files[: limit if limit else None]:
            yield "pmc", p
    if "fda" in sources:
        files = sorted(data_root.glob("fda/**/*/*.json"))
        for p in files[: limit if limit else None]:
            yield "fda", p
    if "clinicaltrials" in sources:
        files = sorted(data_root.glob("clinicaltrials/**/*.xml"))
        for p in files[: limit if limit else None]:
            yield "clinicaltrials", p


def run_extraction(data_root: Path, out_dir: Path, sources: list[str],
                   shard_size_mb: int, limit: Optional[int], ui: UI,
                   resume: bool = True, reset: bool = False,
                   yearly: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    state_path = data_root / "_state" / "extract_state.sqlite"
    state = ExtractState(
        state_path,
        out_dir,
        sources,
        shard_size_mb,
        reset=reset,
    )

    if not resume:
        LOG.info("resume disabled; re-extracting all files")

    writers = _ShardWriters(
        out_dir,
        shard_size_mb * 1024 * 1024,
        yearly=yearly,
        state=state,
        resume=resume,
    )

    stats = {
        "pubmed_files": 0, "pmc_files": 0, "fda_files": 0, "clinicaltrials_files": 0,
        "pubmed_records": 0, "pmc_records": 0, "fda_records": 0,
        "clinicaltrials_records": 0,
        "pubmed_skipped": 0, "pmc_skipped": 0, "fda_skipped": 0,
        "clinicaltrials_skipped": 0,
        "errors": 0,
    }
    t0 = time.time()

    files = list(iter_source_files(data_root, sources, limit))
    done_before = state.done_count()
    if done_before:
        LOG.info("found %d already-extracted files in state", done_before)

    interrupted = False
    try:
        with ui.extract_progress(len(files)) as progress:
            for kind, path in files:
                rel = str(path.relative_to(data_root))
                if resume and state.is_done(rel):
                    stats[f"{kind}_skipped"] += 1
                    progress.file_skipped()
                    LOG.info("skipping already-extracted %s", rel)
                    continue

                progress.set_current(rel, writers.records)
                LOG.info("processing %s (%s)", rel, kind)
                try:
                    years_touched: set[str] = set()
                    if kind == "pubmed":
                        raw = gzip.decompress(path.read_bytes())
                        n = 0
                        for rec in parse_pubmed(raw, rel):
                            writers.write(rec, years_touched)
                            n += 1
                        stats["pubmed_records"] += n
                        stats["pubmed_files"] += 1
                    elif kind == "pmc":
                        n = 0
                        for rec in parse_pmc_tar(path, rel):
                            writers.write(rec, years_touched)
                            n += 1
                            if n % 5000 == 0:
                                LOG.info("  ... %d articles from %s", n, rel)
                        stats["pmc_records"] += n
                        stats["pmc_files"] += 1
                    elif kind == "fda":
                        n = 0
                        for rec in parse_fda_json_file(path, rel):
                            writers.write(rec, years_touched)
                            n += 1
                        stats["fda_records"] += n
                        stats["fda_files"] += 1
                    elif kind == "clinicaltrials":
                        rec = parse_clinicaltrials_xml_file(path, rel)
                        n = 1 if rec else 0
                        if rec:
                            writers.write(rec, years_touched)
                        stats["clinicaltrials_records"] += n
                        stats["clinicaltrials_files"] += 1
                    LOG.info("  -> %d records (running total %d)", n, writers.records)
                    progress.file_done()
                    # Roll each touched writer so the next source file starts on
                    # a fresh shard. This guarantees resume can safely discard
                    # partial shards from an interrupted run.
                    for year in years_touched:
                        writers.writers[year].roll()
                    year_shards = {
                        year: writers.writers[year].current_shard
                        for year in years_touched
                    }
                    state.mark_done(rel, n, year_shards)
                except Exception as exc:
                    stats["errors"] += 1
                    LOG.error("failed on %s: %s", rel, exc)
                    progress.file_failed()
    except KeyboardInterrupt:
        LOG.warning("interrupted by user; finishing partial shard")
        interrupted = True
    finally:
        writers.close()
        state.close()
        elapsed = time.time() - t0

        year_records = {
            year: writer.records for year, writer in writers.writers.items()
        }
        year_shards = {
            year: writer.current_shard for year, writer in writers.writers.items()
        }

        manifest = {
            **stats,
            "skipped_files": (
                stats["pubmed_skipped"] + stats["pmc_skipped"] +
                stats["fda_skipped"] + stats["clinicaltrials_skipped"]
            ),
            "total_records": writers.records,
            "shards": sum(year_shards.values()) if yearly else (year_shards.get("", 0)),
            "shard_size_mb": shard_size_mb,
            "out_dir": str(out_dir),
            "elapsed_sec": round(elapsed, 1),
            "interrupted": interrupted,
            "resume": resume,
            "yearly": yearly,
            "years": {
                year: {"records": year_records.get(year, 0),
                       "shards": year_shards.get(year, 0)}
                for year in sorted(year_shards)
            },
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        if interrupted:
            LOG.info("INTERRUPTED %s", json.dumps(manifest))
        else:
            LOG.info("DONE %s", json.dumps(manifest))

    return manifest
