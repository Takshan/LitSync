Installation
============

LitSync requires Python 3.10 or newer.

Quick install
-------------

The fastest way is with ``uv`` (recommended) or ``pip``:

.. code-block:: bash

   # Using uv
   uv venv && source .venv/bin/activate
   uv pip install -e ".[dev]"

   # Using pip
   python -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"

Or use the repository Makefile:

.. code-block:: bash

   make install   # installs runtime requirements
   make dev       # installs the package in editable mode

From PyPI:

.. code-block:: bash

   pip install litsync

Dependencies
============

LitSync has a deliberately small dependency footprint:

==================  ==========================================================
Package             Purpose
==================  ==========================================================
``requests``        HTTP downloads with retries and resumable ranges.
``rich``            Progress bars, tables, and styled terminal output.
==================  ==========================================================

Optional extras
---------------

==================  ==========================================================
Extra               Purpose
==================  ==========================================================
``dev``             Development tools (``pytest``, ``ruff``).
``docs``            Sphinx and theme packages for building this documentation.
==================  ==========================================================

Install extras together:

.. code-block:: bash

   pip install -e ".[dev,docs]"

Configure environment
---------------------

At minimum set a contact email — it is sent in the ``User-Agent`` header so the
service operators can reach you if your mirror misbehaves:

.. code-block:: bash

   export NCBI_EMAIL=you@example.org

You can also pass it per-run with ``--email``.

Verify the install
------------------

.. code-block:: bash

   litsync --help
   litsync-extract --help

You should see the CLI help for both entry points.
