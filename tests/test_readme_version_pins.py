"""README install pins must equal the pyproject version.

The 0.15.0/0.19.0 pins shipped for several releases because nothing compared
them with `pyproject.toml`. A release bump now fails here until the README
follows.
"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"\bdreamlake(?:@|==)(\d+\.\d+\.\d+)")


def test_readme_pins_match_pyproject_version():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    pins = PIN.findall((ROOT / "README.md").read_text())
    assert pins, "README no longer shows an install pin to check"
    assert set(pins) == {version}, f"README pins {sorted(set(pins))} != pyproject {version}"
