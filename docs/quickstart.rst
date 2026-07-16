Quick Start
===========

This guide gets you from zero to a working mirror in a few minutes.

1. Install LitSync
------------------

.. code-block:: bash

   pip install litsync
   export NCBI_EMAIL=you@example.org

2. Preview a run (dry run)
--------------------------

See what would be downloaded without fetching anything:

.. code-block:: bash

   litsync --data-root /data/literature --email you@example.org \
     --sources pubmed --dry-run

3. Run the mirror
-----------------

.. code-block:: bash

   litsync --data-root /data/literature --email you@example.org \
     --sources pubmed pmc fda clinicaltrials

The first run downloads the full baselines and can take a long time (hundreds of
gigabytes). Subsequent daily runs only fetch new or changed files.

.. tip::

   Start with a single source, e.g. ``--sources pubmed``, to validate your setup
   before enabling all four.

4. Check the results
--------------------

.. code-block:: text

   /data/literature/
     pubmed/baseline/                    pubmed26nXXXX.xml.gz (+ .md5 verified)
     pubmed/updatefiles/                 daily citation deltas
     pmc/oa_bulk/<group>/<fmt>/          baseline + dated incremental .tar.gz
     fda/<category>/<endpoint>/          openFDA bulk snapshot zips + extracted JSON
     clinicaltrials/ctg-public-xml.zip   ClinicalTrials.gov full XML dump
     _state/state.sqlite                 file ledger (status, size, mtime, md5, etag)
     _state/logs/                        dated run logs

5. Extract a test corpus
------------------------

Convert one file per source into sharded JSONL as a quick check:

.. code-block:: bash

   litsync-extract --data-root /data/literature --out ./data/corpus_test \
     --sources pubmed pmc fda clinicaltrials --limit 1

6. Schedule daily updates
-------------------------

Add a cron entry (daily at 02:30):

.. code-block:: text

   30 2 * * *  /path/to/venv/bin/litsync --data-root /data/literature --email you@example.org >> /data/literature/_state/cron.log 2>&1

A run lock (``_state/litsync.lock``) prevents overlapping cron runs, and the exit
code is non-zero if any file failed, so monitoring can alert.
