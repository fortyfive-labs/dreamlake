DreamLake
=========

Python SDK for ML experiment tracking and data storage.

**Version:** |RELEASE|

Installation
------------

.. code-block:: bash

   pip install dreamlake==0.4.2

Usage
-----

.. code-block:: python

   from dreamlake import Session

   with Session(name="data-collection", workspace="robotics", local_path=".dreamlake") as session:
       session.log("Recording started")
       session.params.set(robot="UR5", frequency=100)
       session.track("robot/joint_pos").append(q=[0.1, 0.2, 0.3], _ts=1.0)

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
   cli

.. toctree::
   :maxdepth: 2
   :caption: Examples
   :hidden:

   basic-training
   hyperparameter-search
   model-comparison

.. toctree::
   :maxdepth: 2
   :caption: Development
   :hidden:

   testing
   deployment

