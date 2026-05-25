DreamLake
=========

Python SDK for ML experiment tracking and data storage.

**Version:** |RELEASE|

.. code-block:: bash

   pip install dreamlake

.. code-block:: python

   from dreamlake import Episode

   with Episode(prefix="robotics/data-collection", local_path=".dreamlake") as ep:
       ep.log("Recording started")
       ep.params.set(robot="UR5", frequency=100)
       ep.track("robot/joint_pos").append(q=[0.1, 0.2, 0.3], _ts=1.0)

.. toctree::
   :maxdepth: 2
   :caption: Guide
   :hidden:

   quickstart
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
