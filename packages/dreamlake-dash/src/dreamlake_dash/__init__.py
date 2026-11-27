"""Standalone DreamLake experiment recording, extracted from the ml-dash lineage.

``dash_writer`` logs a run to the project's built-in (managed) Dash service through
DreamLake-minted, short-lived tokens. The DreamLake-native client stays at
``DreamLakeClient(...).experiments``.
"""

from .dash import DashRun, DashWriterError, dash_writer

__all__ = ["DashRun", "DashWriterError", "dash_writer"]

# Native recording is independent of the historical managed writer.
from .experiment import Experiment, experiment
from .native import NativeConnection, NativeExperimentError
from .job_lifecycle import JobLifecycle
from .run import RUN
from .metric_rows import MetricColumn, MetricRowBlock, MetricRowsPage

__all__ += ["Experiment", "experiment", "NativeConnection", "NativeExperimentError", "JobLifecycle", "RUN", "MetricColumn", "MetricRowBlock", "MetricRowsPage"]

from .native import NativeRemoteClient
from .storage import LocalStorage as NativeLocalStorage
from .exceptions import ExperimentError, MetricRowsError, ConfigurationError, NetworkError, StorageError, AuthenticationError
from .exceptions import MlDashError as ExperimentRecordingError
__all__ += ["NativeRemoteClient", "NativeLocalStorage", "ExperimentError", "MetricRowsError", "ConfigurationError", "NetworkError", "StorageError", "AuthenticationError", "ExperimentRecordingError"]

from ._shared import OperationMode, LogLevel
from .buffer import BufferConfig
__all__ += ["OperationMode", "LogLevel", "BufferConfig"]

from importlib.metadata import version as _version, PackageNotFoundError
try:
    __version__ = _version("dreamlake-dash")
except PackageNotFoundError:
    __version__ = "0.1.0"
