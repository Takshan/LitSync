"""Sphinx configuration for LitSync documentation."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# -- Path setup --------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

# -- Project information -----------------------------------------------------
project = "LitSync"
copyright = f"{datetime.now().year}, LitSync"  # noqa: A001
author = "LitSync"
version = "0.0.3"
release = "0.0.3"

# -- General configuration ---------------------------------------------------
extensions = [
    "sphinx_design",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.mathjax",
    "sphinx.ext.todo",
    "sphinx_copybutton",
    "myst_parser",
]

source_suffix = {
    ".rst": None,
    ".md": "markdown",
}

master_doc = "index"
language = "en"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "**.ipynb_checkpoints"]
pygments_style = "sphinx"

todo_include_todos = True

# -- Autodoc / Autosummary ---------------------------------------------------
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "undoc-members": True,
    "show-inheritance": True,
    "special-members": "__init__",
}
autodoc_typehints = "description"
autodoc_class_signature = "separated"
autodoc_mock_imports = []

autosummary_generate = True
autosummary_imported_members = False

# -- Napoleon ----------------------------------------------------------------
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_param = True
napoleon_use_rtype = True

# -- Intersphinx -------------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# -- MyST --------------------------------------------------------------------
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "tasklist",
    "attrs_inline",
    "fieldlist",
]
myst_heading_anchors = 3

# -- HTML output -------------------------------------------------------------
html_theme = "pydata_sphinx_theme"
html_title = "LitSync"
html_short_title = "LitSync"
html_baseurl = "https://takshan.github.io/LitSync"
html_static_path = ["_static"]
html_css_files = ["css/custom.css"]

html_theme_options = {
    "logo": {
        "text": "LitSync",
        "alt_text": "LitSync - incremental biomedical literature mirror",
    },
    "github_url": "https://github.com/Takshan/LitSync",
    "icon_links": [
        {
            "name": "PyPI",
            "url": "https://pypi.org/project/litsync",
            "icon": "fab fa-python",
        },
    ],
    "use_edit_page_button": True,
    "show_toc_level": 2,
    "navbar_align": "content",
    "navbar_end": ["navbar-icon-links"],
    "footer_start": ["copyright"],
    "footer_center": ["sphinx-version"],
    "secondary_sidebar_items": ["page-toc", "edit-this-page", "sourcelink"],
    "announcement": None,
    "navigation_depth": 4,
    "show_nav_level": 2,
    "collapse_navigation": False,
}

html_context = {
    "github_user": "Takshan",
    "github_repo": "LitSync",
    "github_version": "main",
    "doc_path": "docs",
}

html_sidebars = {
    "**": ["search-field", "sidebar-nav-bs"],
}

html_show_sphinx = False
htmlhelp_basename = "LitSyncdoc"

# -- LaTeX output ------------------------------------------------------------
latex_elements = {}
latex_documents = [
    (master_doc, "LitSync.tex", "LitSync Documentation", "LitSync", "manual"),
]

# -- Manual page output ------------------------------------------------------
man_pages = [
    (master_doc, "litsync", "LitSync Documentation", [author], 1),
]

# -- Texinfo output ----------------------------------------------------------
texinfo_documents = [
    (
        master_doc,
        "LitSync",
        "LitSync Documentation",
        author,
        "LitSync",
        "Incremental mirror for PubMed, PMC, FDA, and ClinicalTrials.gov",
        "Miscellaneous",
    ),
]
