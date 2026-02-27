# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys
import subprocess
sys.path.insert(0, os.path.abspath('../src'))

# Get version from package
try:
    from dreamlake import __version__
    version = __version__
except ImportError:
    # Fallback: read from pyproject.toml
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        import tomli as tomllib  # Fallback for older Python

    with open(os.path.join(os.path.dirname(__file__), '..', 'pyproject.toml'), 'rb') as f:
        pyproject = tomllib.load(f)
        version = pyproject['project']['version']

# Get git hash
try:
    git_hash = subprocess.check_output(
        ['git', 'rev-parse', '--short', 'HEAD'],
        cwd=os.path.dirname(__file__),
        stderr=subprocess.DEVNULL
    ).decode('utf-8').strip()
    release = f"{version} ({git_hash})"
except (subprocess.CalledProcessError, FileNotFoundError):
    release = version
    git_hash = "unknown"

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'Dreamlake'
copyright = '2025, Ge Yang, Tom Tao'
author = 'Ge Yang, Tom Tao'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx_autodoc_typehints',
    'myst_parser',
    'sphinx_copybutton',
    'sphinx_design',
    'sphinx_tabs.tabs',
    'sphinxcontrib.mermaid',
    'sphinxext.opengraph',
]

# MyST parser configuration
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "dollarmath",
    "fieldlist",
    "html_admonition",
    "html_image",
    "linkify",
    "replacements",
    "smartquotes",
    "strikethrough",
    "substitution",
    "tasklist",
]

# MyST substitutions for {VERSION} and {GIT_HASH} in markdown files
myst_substitutions = {
    "VERSION": version,
    "GIT_HASH": git_hash,
    "RELEASE": release,
}

# Support both .rst and .md files
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

# RST substitutions (for |VERSION|, |RELEASE|, etc. in .rst files)
rst_prolog = f"""
.. |VERSION| replace:: {version}
.. |RELEASE| replace:: {release}
.. |GIT_HASH| replace:: {git_hash}
"""

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# Autodoc settings
autodoc_default_options = {
    'members': True,
    'member-order': 'bysource',
    'special-members': '__init__',
    'undoc-members': True,
    'exclude-members': '__weakref__'
}

autosummary_generate = True

# Napoleon settings for Google/NumPy style docstrings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True

# Intersphinx mapping
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
}

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'furo'
html_static_path = ['_static']

html_theme_options = {
    "sidebar_hide_name": True,
    "light_logo": "logo_light.png",
    "dark_logo": "logo_dark.png",
    "source_repository": "https://github.com/fortyfive-labs/dreamlake/",
    "source_branch": "main",
    "source_directory": "docs/",
}

# Custom CSS files
html_css_files = [
    'custom.css',
]

# The master toctree document
master_doc = 'index'

# -- Extension configuration -------------------------------------------------

# sphinx-copybutton: Copy button for code blocks
copybutton_prompt_text = r">>> |\.\.\. |\$ |In \[\d*\]: | {2,5}\.\.\.: | {5,8}: "
copybutton_prompt_is_regexp = True

# sphinxext-opengraph: Open Graph meta tags for social media
ogp_site_url = "https://dreamlake.readthedocs.io/"
ogp_site_name = "Dreamlake Documentation"
ogp_description = "ML experiment tracking and data storage"
ogp_type = "website"

# sphinxcontrib-mermaid: Mermaid diagram configuration
mermaid_version = "latest"
