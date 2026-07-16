CLI Reference
=============

LitSync ships two commands: ``litsync`` (the mirror) and ``litsync-extract``
(corpus extraction).

``litsync``
-----------

.. code-block:: text

   litsync --data-root PATH --email EMAIL [options]

Options
~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Flag
     - Description
   * - ``--data-root PATH``
     - **Required.** Root directory for the local mirror.
   * - ``--email EMAIL``
     - Contact email sent in the ``User-Agent``. Defaults to ``$NCBI_EMAIL``;
       one of the two is required.
   * - ``--sources ...``
     - Corpora to mirror: ``pubmed pmc fda clinicaltrials``
       (default: ``pubmed pmc``).
   * - ``--pmc-groups ...``
     - PMC OA groups: ``oa_comm oa_noncomm oa_other`` (default: all three).
   * - ``--pmc-formats ...``
     - PMC formats: ``xml txt`` (default: ``xml``).
   * - ``--fda-endpoints ...``
     - openFDA endpoints to mirror, e.g. ``drug/event drug/label``
       (default: all endpoints).
   * - ``--workers N``
     - Concurrent downloads (default: ``4``). Keep modest; be polite.
   * - ``--max-retries N``
     - Per-file retry attempts (default: ``5``).
   * - ``--timeout SECONDS``
     - HTTP timeout (default: ``60``).
   * - ``--dry-run``
     - Plan only; download nothing.
   * - ``--reverify``
     - Re-download already-downloaded files to verify integrity.
   * - ``--prune``
     - Delete local files no longer present on the server.
   * - ``--count-articles``
     - Count articles in already-downloaded files and exit (no network).
   * - ``--no-rich``
     - Disable Rich progress bars; use plain text output.
   * - ``--verbose``, ``-v``
     - Enable debug logging.

Exit code is non-zero if any file failed, so cron/monitoring can alert.

Examples
~~~~~~~~

.. code-block:: bash

   # Full mirror of all four sources
   litsync --data-root /data/literature --email you@institute.org \
     --sources pubmed pmc fda clinicaltrials

   # Only two openFDA endpoints
   litsync --data-root /data/literature --email you@institute.org \
     --sources fda --fda-endpoints drug/event drug/label

   # Daily cron entry (02:30)
   30 2 * * *  /path/to/venv/bin/litsync --data-root /data/literature \
     --email you@institute.org >> /data/literature/_state/cron.log 2>&1

``litsync-extract``
-------------------

.. code-block:: text

   litsync-extract --data-root PATH --out PATH [options]

Options
~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Flag
     - Description
   * - ``--data-root PATH``
     - Mirror root to read (default: ``./data/literature``).
   * - ``--out PATH``
     - Output directory for sharded JSONL (default: ``./data/corpus``).
   * - ``--sources ...``
     - Sources to extract (default: ``pubmed pmc``).
   * - ``--shard-size-mb N``
     - Maximum shard size in MB (default: ``256``).
   * - ``--limit N``
     - Extract at most *N* files per source (quick tests).
   * - ``--resume`` / ``--no-resume``
     - Skip source files already recorded in resume state (default: on).
   * - ``--reset``
     - Clear resume state and re-extract from the beginning.
   * - ``--yearly``
     - Split output into subdirectories by record year.
   * - ``--require-abstract``
     - Skip records with an empty abstract.
   * - ``--require-body``
     - Skip records with an empty body.
   * - ``--require-text``
     - Skip records with neither abstract nor body text.
   * - ``--no-rich``
     - Disable Rich progress bars.
   * - ``--verbose``, ``-v``
     - Enable debug logging.

Examples
~~~~~~~~

.. code-block:: bash

   # Full corpus extraction (hours; very large)
   litsync-extract --data-root /data/literature --out /data/corpus \
     --sources pubmed pmc fda clinicaltrials

   # Quick check: one file per source
   litsync-extract --data-root /data/literature --out ./data/corpus_test \
     --sources pubmed pmc fda clinicaltrials --limit 1

Makefile shortcuts
------------------

The repository Makefile wraps both commands:

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Target
     - Description
   * - ``make install`` / ``make dev``
     - Install requirements / editable package.
   * - ``make sync EMAIL=you@x.org``
     - Run the incremental mirror.
   * - ``make dry-run``
     - Plan only, download nothing.
   * - ``make reverify`` / ``make prune`` / ``make count-articles``
     - Maintenance operations.
   * - ``make pubmed`` / ``make pmc`` / ``make fda`` / ``make clinicaltrials``
     - Sync a single source.
   * - ``make extract`` / ``make extract-test``
     - Full extraction / one-file-per-source test.

Environment variables
---------------------

``NCBI_EMAIL``
   Default contact email when ``--email`` is not given.
