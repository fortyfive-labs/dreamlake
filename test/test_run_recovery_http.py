"""Run the committed manual HTTP contract check in the normal suite."""
import runpy
from pathlib import Path


def test_optional_recovery_fields_over_real_http():
    script = Path(__file__).parents[1] / "scripts" / "test_run_recovery_http.py"
    check = runpy.run_path(str(script))["check_recovery_contract"]
    assert check() == {"requests": 6, "writes": 0, "cleanup": True}
