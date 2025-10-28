Dreamlake Documentation
=======================

A simple and flexible SDK for ML experiment tracking and data storage.

Features
--------

* **Three Usage Styles**: Decorator, context manager, or direct instantiation
* **Dual Operation Modes**: Remote (API server) or local (filesystem)
* **Auto-creation**: Automatically creates namespace, workspace, and folder hierarchy
* **Upsert Behavior**: Updates existing sessions or creates new ones
* **Simple API**: Minimal configuration, maximum flexibility

Installation
------------

Using uv (recommended):

.. code-block:: bash

   uv add dreamlake

Using pip:

.. code-block:: bash

   pip install dreamlake

Quick Start
-----------

Remote Mode (with API Server)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from dreamlake import Session

   with Session(
       name="my-experiment",
       workspace="my-workspace",
       remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
       api_key="your-jwt-token"
   ) as session:
       print(f"Session ID: {session.id}")

Local Mode (Filesystem)
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from dreamlake import Session

   with Session(
       name="my-experiment",
       workspace="my-workspace",
       local_path=".dreamlake"
   ) as session:
       pass  # Your code here

Documentation
-------------

.. toctree::
   :maxdepth: 2
   :caption: Getting Started

   getting-started
   api-quick-reference
   examples

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   sessions
   logging
   parameters
   tracks
   files
   local-vs-remote
   complete-examples

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/modules

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
