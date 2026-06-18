# litsync — incremental PubMed + PMC + FDA + ClinicalTrials.gov mirror

A modern, daily-runnable CLI for mirroring bulk biomedical datasets. It tracks every
file in a SQLite state DB so re-runs do the minimum work: already-verified immutable
files are skipped with no network request beyond the directory/manifest listing.

## Install

```bash
pip install -e .
```

Or use the Makefile:

```bash
make install
make dev
```

## Quick start

```bash
litsync --data-root /data/literature --email you@institute.org
```

Common options:

```bash
litsync --data-root /data/literature --email you@institute.org \
  --sources pubmed pmc fda clinicaltrials \
  --fda-endpoints drug/event drug/label
```

```bash
--sources pubmed pmc fda clinicaltrials   # which corpora (default: all four)
--fda-endpoints drug/event drug/label     # default: all openFDA endpoints
--pmc-groups oa_comm oa_noncomm oa_other
--pmc-formats xml txt                     # default: xml
--workers 4                               # concurrent downloads (keep modest; be polite)
--dry-run                                 # plan only, download nothing
--reverify                                # re-download local files (integrity audit)
--prune                                   # delete local files no longer on the server
--count-articles                          # count articles in already-downloaded files (no network)
--no-rich                                 # disable Rich progress bars / tables
```

## On-disk layout

```
/data/literature/
  pubmed/baseline/                    pubmed26nXXXX.xml.gz (+ .md5 verified)
  pubmed/updatefiles/                 daily citation deltas
  pmc/oa_bulk/<group>/<fmt>/          baseline + dated incremental .tar.gz
  pmc/oa_file_list.csv                PMCID <-> PMID id map
  fda/<category>/<endpoint>/          openFDA bulk snapshot zips + extracted JSON
  clinicaltrials/ctg-public-xml.zip   ClinicalTrials.gov full XML dump
  clinicaltrials/ctg-public-xml/      extracted study XML files
  _state/state.sqlite                 file ledger (status, size, mtime, md5, etag, attempts)
  _state/logs/                        dated run logs
  _state/litsync.lock                 run lock (prevents overlapping cron runs)
```

## Cron (daily 02:30)

```cron
30 2 * * *  /path/to/venv/bin/litsync --data-root /data/literature --email you@institute.org >> /data/literature/_state/cron.log 2>&1
```

## Extract corpus to sharded JSONL

```bash
litsync-extract --data-root /data/literature --out /data/corpus \
  --sources pubmed pmc fda clinicaltrials
```

Or with Make:

```bash
make extract DATA_ROOT=/data/literature CORPUS_OUT=/data/corpus
make extract-test DATA_ROOT=/data/literature
```

## Integrity model

- **PubMed**: every `.xml.gz` is verified against its NCBI `.md5` sidecar.
- **PMC**: bulk packages have no md5 sidecar, so they are verified by `Content-Length`
  and an `ETag` is recorded for change detection.
- **openFDA / ClinicalTrials.gov**: these sources publish full snapshots. The downloader
  detects changed snapshots via `ETag` / `Last-Modified` / `Content-Length` and only
  re-downloads when the snapshot changes. When a snapshot changes it is extracted
  again next to the zip file.
- Downloads are atomic (`.part` -> rename) and resumable via HTTP Range.
- Exit code is non-zero if any file failed, so cron/monitoring can alert.

## Notes on sources

- **openFDA** bulk data is zipped JSON. The manifest is fetched from `https://api.fda.gov/download.json`.
  Each endpoint partition becomes one downloaded/extracted unit.
- **ClinicalTrials.gov** bulk data is the full public XML dump from
  `https://clinicaltrials.gov/api/legacy/public-xml?format=zip`. One XML file per study.
- Both sources are snapshots, not daily deltas. Daily runs are still cheap because unchanged
  snapshots are skipped; changed snapshots are replaced in full.

