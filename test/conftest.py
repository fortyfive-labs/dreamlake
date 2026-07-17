"""Pytest configuration and fixtures for Dreamlake tests."""
import os
import sys
import tempfile
import shutil
from pathlib import Path
import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dreamlake import Episode
import msgpack


def read_msgpack_track_file(file_path):
    """
    Helper function to read msgpack track files and expand columnar format.

    Returns a list of data points in the format: [{"data": {...}, "index": 0}, ...]
    """
    data_points = []
    with open(file_path, "rb") as f:
        unpacker = msgpack.Unpacker(f, raw=False)
        current_index = 0
        for obj in unpacker:
            if isinstance(obj, dict):
                # Check if columnar format (all values are lists of same length)
                if all(isinstance(v, list) for v in obj.values()) and obj:
                    lengths = [len(v) for v in obj.values()]
                    if len(set(lengths)) == 1 and lengths[0] > 0:
                        # Columnar format - expand to rows
                        num_rows = lengths[0]
                        for i in range(num_rows):
                            row = {key: values[i] for key, values in obj.items()}
                            data_points.append({"data": row, "index": current_index})
                            current_index += 1
                    else:
                        # Single row
                        data_points.append({"data": obj, "index": current_index})
                        current_index += 1
                else:
                    # Single row
                    data_points.append({"data": obj, "index": current_index})
                    current_index += 1
    return data_points


# Configuration
#
# Remote tests are opt-in: set DREAMLAKE_URL to a running dreamlake-server
# (dev default: http://localhost:10334) to enable them. When DREAMLAKE_URL is
# unset or the server is unreachable, all remote tests are skipped so the
# suite passes on a machine with nothing running.
REMOTE_SERVER_URL = os.environ.get("DREAMLAKE_URL")
TEST_USERNAME = "test-user"

_remote_available_cache = None


def remote_server_available():
    """
    Check (once) whether a dreamlake-server is reachable at DREAMLAKE_URL.

    Requires an actual dreamlake-server /health response (JSON with
    status "ok") — a plain 200 is not enough, because other services
    (e.g. the frontend dev server) also answer /health with HTML.
    """
    global _remote_available_cache
    if _remote_available_cache is not None:
        return _remote_available_cache

    if not REMOTE_SERVER_URL:
        _remote_available_cache = False
        return False

    import httpx
    try:
        response = httpx.get(f"{REMOTE_SERVER_URL}/health", timeout=2.0)
        body = response.json()
        _remote_available_cache = (
            response.status_code == 200 and body.get("status") == "ok"
        )
    except Exception:
        _remote_available_cache = False
    return _remote_available_cache


REMOTE_SKIP_REASON = (
    "Remote server not available (set DREAMLAKE_URL to a running dreamlake-server, "
    "e.g. http://localhost:10334, and DREAMLAKE_API_KEY to enable remote tests)"
)


# ── Known dreamlake-server bugs (verified live, 2026-07-17) ───────────────
#
# These marks gate remote tests whose server-side handlers are currently
# broken. The SDK sends correct requests to the correct routes; the requests
# reach the handlers and fail inside the server. Remove each mark once the
# corresponding server fix lands.
#
# Fixed by dreamlake-server#61 / PR #65 (gates removed, verified live):
# parameters create, the 'fatal' log level, and the descendants response
# schema. Still broken (dreamlake-server#67): track append and full file
# upload — services/tracks.ts and services/files.ts call Prisma models
# (TrackMetadata / TrackBuffer / TrackChunk / File) that the 3b2779a schema
# redesign deleted, so both 500 one layer past the #65 fixes.
server_tracks_bug = pytest.mark.skip(
    reason="dreamlake-server bug (dreamlake-server#67): track append 500s — the "
    "Track row now creates correctly, but services/tracks.ts still calls Prisma "
    "models (TrackMetadata/TrackBuffer/TrackChunk) deleted by the 3b2779a schema "
    "redesign, so the append path dies at the deleted-model call",
)
server_files_bug = pytest.mark.skip(
    reason="dreamlake-server bug (dreamlake-server#67): file upload 500s — the "
    "multipart route now accepts the request, but services/files.ts still calls "
    "the Prisma File model deleted by the 3b2779a schema redesign",
)
server_content_bugs = pytest.mark.skip(
    reason="dreamlake-server bugs (dreamlake-server#67): track append 500s and "
    "file upload 500s (see server_tracks_bug / server_files_bug in conftest.py)",
)


@pytest.fixture
def temp_workspace(tmp_path):
    """
    Temporary workspace directory for tests.

    Uses pytest's tmp_path fixture which creates a unique temporary directory
    that is automatically cleaned up after the test.
    """
    return tmp_path


@pytest.fixture
def local_episode(temp_workspace):
    """
    Create a test episode in local mode (ML-Dash compatible API).

    Returns a function that creates episodes with default config but allows overrides.
    """
    def _create_episode(prefix="test-workspace/test-episode", **kwargs):
        defaults = {
            "root": str(temp_workspace),
        }
        defaults.update(kwargs)
        return Episode(prefix=prefix, **defaults)

    return _create_episode


@pytest.fixture
def remote_episode():
    """
    Create a test episode in remote mode (ML-Dash compatible API).

    Returns a function that creates remote episodes against DREAMLAKE_URL.
    Use the @pytest.mark.remote marker for tests that require a running server.
    Requires DREAMLAKE_API_KEY environment variable to be set.
    """
    if not remote_server_available():
        pytest.skip(REMOTE_SKIP_REASON)
    if not os.environ.get("DREAMLAKE_API_KEY"):
        pytest.skip("DREAMLAKE_API_KEY not set (required for remote tests)")

    def _create_episode(prefix="test-workspace/test-episode", **kwargs):
        defaults = {
            "url": REMOTE_SERVER_URL,
        }
        defaults.update(kwargs)
        # Note: Requires DREAMLAKE_API_KEY environment variable
        return Episode(prefix=prefix, **defaults)

    return _create_episode


@pytest.fixture(params=["local", "remote"])
def any_episode(request):
    """
    Parametrized fixture that runs tests with both local and remote episodes.

    Tests using this fixture will run twice: once with local mode and once with remote mode.
    Remote tests will be skipped if the server is not available.
    """
    if request.param == "local":
        return request.getfixturevalue("local_episode")
    else:
        if request.node.get_closest_marker("skip_remote"):
            pytest.skip("Test explicitly skips remote mode")
        # remote_episode itself skips when the server is unavailable
        return request.getfixturevalue("remote_episode")


@pytest.fixture
def sample_files(tmp_path):
    """
    Create sample files for file upload tests.

    Returns a dict with paths to created files:
    - model: model.txt (simulated model weights)
    - config: config.json (configuration file)
    - results: results.csv (CSV results)
    - image: test_image.png (small binary file)
    - large: large_file.bin (larger binary file)
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
    results_file = files_dir / "results.csv"
    results_file.write_text("epoch,loss,accuracy\n1,0.5,0.85\n2,0.3,0.90\n3,0.2,0.93\n")

    # Create a small binary file (simulating an image)
    image_file = files_dir / "test_image.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # Create a larger binary file
    large_file = files_dir / "large_file.bin"
    large_file.write_bytes(b"\x00" * (1024 * 100))  # 100 KB

    return {
        "model": str(model_file),
        "config": str(config_file),
        "results": str(results_file),
        "image": str(image_file),
        "large": str(large_file),
        "dir": str(files_dir),
    }


@pytest.fixture
def sample_data():
    """
    Sample data for testing tracks and parameters.

    Returns a dict with various test data structures.
    """
    return {
        "simple_params": {
            "learning_rate": 0.001,
            "batch_size": 32,
            "epochs": 100,
        },
        "nested_params": {
            "model": {
                "architecture": "resnet50",
                "pretrained": True,
                "layers": {
                    "conv1": {"filters": 64, "kernel": 3},
                    "conv2": {"filters": 128, "kernel": 3},
                }
            },
            "optimizer": {
                "type": "adam",
                "beta1": 0.9,
                "beta2": 0.999,
                "lr": 0.001,
            }
        },
        "track_data": [
            {"value": 0.5, "epoch": 0, "step": 0},
            {"value": 0.4, "epoch": 1, "step": 100},
            {"value": 0.3, "epoch": 2, "step": 200},
            {"value": 0.25, "epoch": 3, "step": 300},
            {"value": 0.2, "epoch": 4, "step": 400},
        ],
        "multi_metric_data": [
            {"epoch": 0, "train_loss": 0.5, "val_loss": 0.6, "accuracy": 0.7},
            {"epoch": 1, "train_loss": 0.4, "val_loss": 0.5, "accuracy": 0.75},
            {"epoch": 2, "train_loss": 0.3, "val_loss": 0.4, "accuracy": 0.8},
        ]
    }


@pytest.fixture
def check_remote_available():
    """
    Check if remote server is available.

    Returns True if the server responds, False otherwise.
    Useful for conditional test skipping.
    """
    return remote_server_available()


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "remote: mark test as requiring remote server (set DREAMLAKE_URL to enable)"
    )
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers",
        "skip_remote: mark test to skip remote mode in parametrized tests"
    )
    config.addinivalue_line(
        "markers",
        "local_only: mark test to run only in local mode"
    )
    config.addinivalue_line(
        "markers",
        "remote_only: mark test to run only in remote mode"
    )


def pytest_collection_modifyitems(items):
    """
    Modify test collection to handle remote tests gracefully.

    Adds skip markers to remote tests if the server is not available.
    """
    server_available = remote_server_available()

    skip_remote = pytest.mark.skip(reason=REMOTE_SKIP_REASON)

    for item in items:
        # Skip remote-only tests if server not available
        if "remote_only" in item.keywords and not server_available:
            item.add_marker(skip_remote)

        # Skip remote tests if marked and server not available
        if "remote" in item.keywords and not server_available:
            item.add_marker(skip_remote)
