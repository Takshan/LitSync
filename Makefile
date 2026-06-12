# Makefile for litsync — incremental PubMed + PMC + FDA + ClinicalTrials.gov mirror

# --- Configurable variables (override on the command line, e.g. `make sync DATA_ROOT=/data/literature`) ---
PYTHON        ?= python
DATA_ROOT     ?= ./data/literature
EMAIL         ?= $(NCBI_EMAIL)
WORKERS       ?= 4
SOURCES       ?= pubmed pmc fda clinicaltrials
FDA_ENDPOINTS ?=                       # e.g. "drug/event drug/label"; empty = all
CORPUS_OUT    ?= ./data/corpus
SHARD_MB      ?= 256

SYNC = litsync --data-root $(DATA_ROOT) --email $(EMAIL) \
       --sources $(SOURCES) --workers $(WORKERS) \
       $(if $(FDA_ENDPOINTS),--fda-endpoints $(FDA_ENDPOINTS),)

EXTRACT = litsync-extract --data-root $(DATA_ROOT) --out $(CORPUS_OUT) \
       --sources $(SOURCES) --shard-size-mb $(SHARD_MB)

.PHONY: help install dev check-email sync dry-run reverify prune count-articles pubmed pmc \
        fda clinicaltrials extract extract-test clean-pyc

check-email:
	@if [ -z "$(EMAIL)" ]; then \
	  echo "ERROR: no contact email set."; \
	  echo "  Run:  make sync EMAIL=you@example.org"; \
	  echo "  Or:   export NCBI_EMAIL=you@example.org   (then: make sync)"; \
	  exit 1; \
	fi

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install Python dependencies
	$(PYTHON) -m pip install -r requirements.txt

dev:  ## Install package in editable mode
	$(PYTHON) -m pip install -e .

sync: check-email  ## Run the incremental mirror
	$(SYNC)

dry-run: check-email  ## Plan only, download nothing
	$(SYNC) --dry-run

reverify: check-email  ## Re-hash local files (integrity audit)
	$(SYNC) --reverify

prune: check-email  ## Delete local files no longer on the server
	$(SYNC) --prune

count-articles: check-email  ## Count articles in already-downloaded files (no network; one-time backfill)
	$(SYNC) --count-articles

pubmed:  ## Sync PubMed only
	$(MAKE) sync SOURCES=pubmed

pmc:  ## Sync PMC only
	$(MAKE) sync SOURCES=pmc

fda:  ## Sync openFDA only
	$(MAKE) sync SOURCES=fda

clinicaltrials:  ## Sync ClinicalTrials.gov only
	$(MAKE) sync SOURCES=clinicaltrials

extract:  ## Extract full corpus into sharded JSONL (hours; very large source)
	$(EXTRACT)

extract-test:  ## Extract just 1 file per source into ./data/corpus_test (quick check)
	litsync-extract --data-root $(DATA_ROOT) --out ./data/corpus_test \
	  --sources pubmed pmc fda clinicaltrials --limit 1

clean-pyc:  ## Remove Python cache files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
