"""Experiment writers.

``dash_writer`` logs a run to the project's built-in (managed) Dash service through
DreamLake-minted, short-lived tokens. The DreamLake-native client stays at
``DreamLakeClient(...).experiments``.
"""

from .dash import DashRun, DashWriterError, dash_writer

__all__ = ["DashRun", "DashWriterError", "dash_writer"]

# Native recording is independent of the historical managed writer.
from ._native.experiment import Experiment, experiment
from ._native.native import NativeConnection, NativeExperimentError
from ._native.job_lifecycle import JobLifecycle
from ._native.run import RUN
from ._native.metric_rows import MetricColumn, MetricRowBlock, MetricRowsPage

__all__ += ["Experiment", "experiment", "NativeConnection", "NativeExperimentError", "JobLifecycle", "RUN", "MetricColumn", "MetricRowBlock", "MetricRowsPage"]

from ._native.native import NativeRemoteClient
from ._native.storage import LocalStorage as NativeLocalStorage
from ._native.exceptions import ExperimentError, MetricRowsError, ConfigurationError, NetworkError, StorageError, AuthenticationError
from ._native.exceptions import MlDashError as ExperimentRecordingError
__all__ += ["NativeRemoteClient", "NativeLocalStorage", "ExperimentError", "MetricRowsError", "ConfigurationError", "NetworkError", "StorageError", "AuthenticationError", "ExperimentRecordingError"]
