"""Pytest configuration and fixtures for Dreamlake tests."""
import os
import sys
import tempfile
import shutil
from pathlib import Path
import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dreamlake import Session


@pytest.fixture
def temp_workspace(tmp_path):
    """
    Temporary workspace directory for tests.

    Uses pytest's tmp_path fixture which creates a unique temporary directory
    that is automatically cleaned up after the test.
    """
    return tmp_path


@pytest.fixture
def local_session(temp_workspace):
    """
    Create a test session in local mode.

    Returns a function that creates sessions with default config but allows overrides.
    """
    def _create_session(name="test-session", workspace="test-workspace", **kwargs):
        defaults = {
            "local_path": str(temp_workspace),
        }
        defaults.update(kwargs)
        return Session(name=name, workspace=workspace, **defaults)

    return _create_session


@pytest.fixture
def sample_files(tmp_path):
    """
    Create sample files for file upload tests.

    Returns a dict with paths to created files:
    - model: model.txt (simulated model weights)
    - config: config.json (configuration file)
    - results: results.txt (CSV-like results)
    """
    files_dir = tmp_path / "sample_files"
    files_dir.mkdir()

    # Create model file
    model_file = files_dir / "model.txt"
    model_file.write_text("Simulated model weights\n" * 10)

    # Create config file
    config_file = files_dir / "config.json"
    config_file.write_text('{"model": "resnet50", "lr": 0.001, "batch_size": 32}')

    # Create results file
    results_file = files_dir / "results.txt"
    results_file.write_text("Epoch,Loss,Accuracy\n1,0.5,0.85\n2,0.3,0.90\n")

    return {
        "model": str(model_file),
        "config": str(config_file),
        "results": str(results_file),
        "dir": str(files_dir),
    }


@pytest.fixture
def remote_server_url():
    """
    Remote server URL from environment variable.

    Returns None if not set, which will cause remote tests to be skipped.
    """
    return os.getenv("DREAMLAKE_SERVER_URL")


@pytest.fixture
def test_username():
    """Test username for remote authentication."""
    return os.getenv("DREAMLAKE_TEST_USER", "test-user")


@pytest.fixture
def remote_session(remote_server_url, test_username, temp_workspace):
    """
    Create a test session in remote mode.

    Returns a function that creates remote sessions, or None if server URL not configured.
    Skips tests that require this fixture if remote server is not available.
    """
    if not remote_server_url:
        pytest.skip("Remote server URL not configured (set DREAMLAKE_SERVER_URL)")

    def _create_session(name="test-session", workspace="test-workspace", **kwargs):
        defaults = {
            "remote": remote_server_url,
            "user_name": test_username,
        }
        defaults.update(kwargs)
        return Session(name=name, workspace=workspace, **defaults)

    return _create_session


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "remote: mark test as requiring remote server (skipped if not available)"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
