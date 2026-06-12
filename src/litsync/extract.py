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
    def __init__(self, out_dir: Path, shard_bytes: int, prefix: str = "corpus"):
        self.out_dir = out_dir
        self.shard_bytes = shard_bytes
        self.prefix = prefix
        self.idx = 0
        self.bytes = 0
        self.records = 0
        self.fh = None
        self._roll()

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
        if self.bytes and self.bytes + len(data) > self.shard_bytes:
            self._roll()
        self.fh.write(line)
        self.bytes += len(data)
        self.records += 1

    def close(self):
        if self.fh:
            self.fh.close()


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
                   shard_size_mb: int, limit: Optional[int]) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    writer = ShardWriter(out_dir, shard_size_mb * 1024 * 1024)

    stats = {"pubmed_files": 0, "pmc_files": 0, "fda_files": 0, "clinicaltrials_files": 0,
             "pubmed_records": 0, "pmc_records": 0, "fda_records": 0,
             "clinicaltrials_records": 0, "errors": 0}
    t0 = time.time()

    for kind, path in iter_source_files(data_root, sources, limit):
        rel = str(path.relative_to(data_root))
        LOG.info("processing %s (%s)", rel, kind)
        try:
            if kind == "pubmed":
                raw = gzip.decompress(path.read_bytes())
                n = 0
                for rec in parse_pubmed(raw, rel):
                    writer.write(rec)
                    n += 1
                stats["pubmed_records"] += n
                stats["pubmed_files"] += 1
            elif kind == "pmc":
                n = 0
                for rec in parse_pmc_tar(path, rel):
                    writer.write(rec)
                    n += 1
                    if n % 5000 == 0:
                        LOG.info("  ... %d articles from %s", n, rel)
                stats["pmc_records"] += n
                stats["pmc_files"] += 1
            elif kind == "fda":
                n = 0
                for rec in parse_fda_json_file(path, rel):
                    writer.write(rec)
                    n += 1
                stats["fda_records"] += n
                stats["fda_files"] += 1
            elif kind == "clinicaltrials":
                rec = parse_clinicaltrials_xml_file(path, rel)
                n = 1 if rec else 0
                if rec:
                    writer.write(rec)
                stats["clinicaltrials_records"] += n
                stats["clinicaltrials_files"] += 1
            LOG.info("  -> %d records (running total %d)", n, writer.records)
        except Exception as exc:
            stats["errors"] += 1
            LOG.error("failed on %s: %s", rel, exc)

    writer.close()
    elapsed = time.time() - t0

    manifest = {
        **stats,
        "total_records": writer.records,
        "shards": writer.idx,
        "shard_size_mb": shard_size_mb,
        "out_dir": str(out_dir),
        "elapsed_sec": round(elapsed, 1),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    LOG.info("DONE %s", json.dumps(manifest))
    return manifest
