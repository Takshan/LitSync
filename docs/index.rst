.. LitSync documentation master file

LitSync
=======

**Incremental mirror for PubMed, PMC, FDA, and ClinicalTrials.gov**

LitSync is a daily-runnable CLI for mirroring bulk biomedical datasets. It tracks every
file in a SQLite state database, so re-runs do the minimum work: already-verified
immutable files are skipped with no network request beyond the directory/manifest listing.

.. grid:: 1 2 2 2
   :gutter: 3

   .. grid-item-card:: :fas:`rocket` Quick Start
      :link: quickstart
      :link-type: doc

      Install LitSync and run your first mirror in five minutes.

   .. grid-item-card:: :fas:`book-open` User Guide
      :link: user-guide
      :link-type: doc

      Learn about the on-disk layout, integrity model, cron setup, and best practices.

   .. grid-item-card:: :fas:`terminal` CLI Reference
      :link: cli
      :link-type: doc

      Complete reference for ``litsync`` and ``litsync-extract`` commands and options.

   .. grid-item-card:: :fas:`code` API Reference
      :link: api/index
      :link-type: doc

      Python API for configuration, syncing, sources, state, and extraction.

What LitSync does
-----------------

1. **Plan** — lists the remote directories/manifests of the enabled sources.
2. **Diff** — compares each remote file against the SQLite ledger and local disk.
3. **Download** — fetches only new or changed files, atomically and resumably.
4. **Verify** — checks MD5 sidecars (PubMed) or size/ETag (PMC, FDA, ClinicalTrials.gov).
5. **Extract** — converts the mirror into sharded JSONL corpora with ``litsync-extract``.

.. code-block:: bash

   litsync --data-root /data/literature --email you@institute.org

Where to go next
----------------

.. toctree::
   :maxdepth: 2
   :hidden:

   install
   quickstart
   user-guide
   cli
   api/index

* :doc:`install` — set up the package.
* :doc:`quickstart` — run a first mirror and extract a test corpus.
* :doc:`user-guide` — understand the mirror layout and integrity model.
* :doc:`cli` — look up every command and flag.
* :doc:`api/index` — use LitSync as a Python library.
