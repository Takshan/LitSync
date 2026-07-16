User Guide
==========

This guide explains how LitSync organizes the mirror, keeps incremental runs cheap,
and guarantees file integrity.

On-disk layout
--------------

Everything lives under the ``--data-root`` directory:

.. code-block:: text

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

The incremental model
---------------------

Every remote file is recorded in ``_state/state.sqlite`` with its status, size,
mtime, MD5, ETag, and attempt count. On each run LitSync:

1. **Plans** — lists the remote directories (PubMed/PMC FTP listings) or manifests
   (openFDA ``download.json``, ClinicalTrials.gov snapshot).
2. **Diffs** — a file already marked ``verified`` in the ledger *and* present on
   disk is skipped without any further network request.
3. **Downloads** — new or changed files are fetched with a thread pool
   (``--workers``, keep it modest to be polite to the servers).
4. **Verifies** — integrity checks depend on the source (see below).
5. **Reports** — a summary table of skipped/downloaded/verified/failed per source;
   the exit code is non-zero if anything failed.

Downloads are atomic (``.part`` → rename) and resumable via HTTP ``Range`` requests,
so interrupted runs continue where they stopped.

Integrity model
---------------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Source
     - Verification
   * - **PubMed**
     - Every ``.xml.gz`` is verified against its NCBI ``.md5`` sidecar. Files are
       immutable: once verified, they are never re-fetched.
   * - **PMC**
     - Bulk packages have no MD5 sidecar, so they are verified by ``Content-Length``
       and an ``ETag`` is recorded for change detection.
   * - **openFDA**
     - Full snapshot zips per endpoint partition. Changed snapshots are detected via
       ``ETag`` / ``Last-Modified`` / ``Content-Length`` and re-downloaded in full,
       then extracted next to the zip.
   * - **ClinicalTrials.gov**
     - One full public XML dump. Same snapshot-change detection as openFDA; changed
       snapshots are replaced and re-extracted.

.. note::

   openFDA and ClinicalTrials.gov publish *snapshots*, not daily deltas. Daily runs
   are still cheap because unchanged snapshots are skipped; changed snapshots are
   replaced in full.

Maintenance operations
----------------------

``--reverify``
   Re-download already-downloaded files to audit integrity (re-hash and compare).

``--prune``
   Delete local files that no longer exist on the server.

``--count-articles``
   Count articles in already-downloaded local files and exit (no network). Useful
   as a one-time backfill of per-source article totals.

Logging and locking
-------------------

* Per-day log files are written to ``_state/logs/litsync_YYYY-MM-DD.log``.
* Terminal output uses Rich progress bars; use ``--no-rich`` for plain text
  (automatic in non-terminal contexts such as cron).
* ``-v`` / ``--verbose`` enables debug logging to the log file and terminal.
* A filesystem lock (``_state/litsync.lock``) ensures only one run is active at a
  time, so overlapping cron invocations exit immediately instead of corrupting state.

Extracting a corpus
-------------------

``litsync-extract`` converts the mirror into sharded JSONL, one record per article
or study, with resume support:

.. code-block:: bash

   litsync-extract --data-root /data/literature --out /data/corpus \
     --sources pubmed pmc fda clinicaltrials --shard-size-mb 256

Useful options: ``--yearly`` (split output by record year), ``--resume`` /
``--no-resume``, ``--reset``, and the content filters ``--require-abstract``,
``--require-body``, ``--require-text``.

See :doc:`cli` for the full option list.
