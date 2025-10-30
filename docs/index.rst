Welcome to DreamLake
====================

**DreamLake** is a lightweight Python SDK for ML experiment tracking and data storage.

Track your machine learning experiments with zero setup. Start locally on your laptop, then seamlessly scale to a remote server when you need team collaboration. No configuration files, no complex setup - just clean, intuitive code.

**Key highlights:**

- **Zero setup** - Start tracking in 60 seconds with local filesystem storage
- **Dual modes** - Work offline (local) or collaborate (remote server)
- **Fluent API** - Intuitive builder pattern for logs, parameters, metrics, and files

Installation
------------

.. code-block:: bash

   pip install dreamlake

Quick Example
-------------

.. code-block:: python
   :linenos:

   from dreamlake import Session

   with Session(name="my-experiment", workspace="my-workspace", local_path=".dreamlake") as session:
       # Log messages
       session.log("Training started")

       # Track parameters
       session.parameters().set(learning_rate=0.001, batch_size=32)

       # Track metrics
       session.track("loss").append(value=0.5, epoch=1)

.. toctree::
   :maxdepth: 2
   :caption: Getting Started
   :hidden:

   overview
   quickstart

.. toctree::
   :maxdepth: 2
   :caption: Tutorials
   :hidden:

   sessions
   logging
   parameters
   tracks
   files

.. toctree::
   :maxdepth: 2
   :caption: Examples
   :hidden:

   basic-training
   hyperparameter-search
   model-comparison

