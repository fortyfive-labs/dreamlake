"""Experiment writers.

``dash_writer`` logs a run to the project's built-in (managed) Dash service through
DreamLake-minted, short-lived tokens. The DreamLake-native client stays at
``DreamLakeClient(...).experiments``.
"""

from .dash import DashRun, DashWriterError, dash_writer

__all__ = ["DashRun", "DashWriterError", "dash_writer"]
