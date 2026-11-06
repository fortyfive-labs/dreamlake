"""Deprecated import path; use :mod:`dreamlake.api._memos_mapped`."""
from importlib import import_module
import sys

sys.modules[__name__] = import_module("._memos_mapped", __package__)
