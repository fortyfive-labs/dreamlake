"""Deprecated import path; use :mod:`dreamlake.api.memos`."""
from importlib import import_module
import sys

sys.modules[__name__] = import_module(".memos", __package__)
