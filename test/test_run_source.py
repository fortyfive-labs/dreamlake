import base64
import hashlib
import os

import pytest

from dreamlake.api._run_source import RunConfigurationError, collect_source


def test_explicit_non_git_files_preserve_bytes_and_mode(tmp_path):
    (tmp_path / "script.py").write_bytes(b"print('hello')\n")
    (tmp_path / "script.py").chmod(0o755)
    (tmp_path / "unselected-secret").write_bytes(b"private")
    source = collect_source(["script.py"], tmp_path)
    assert len(source) == 1
    assert base64.b64decode(source[0]["contentBase64"]) == b"print('hello')\n"
    assert source[0]["sha256"] == hashlib.sha256(b"print('hello')\n").hexdigest()
    assert source[0]["mode"] == 493
    assert collect_source([], tmp_path) == []


def test_reject_traversal_duplicates_links_and_nonfiles(tmp_path):
    (tmp_path / "file").write_text("secret")
    (tmp_path / "link").symlink_to(tmp_path / "file")
    (tmp_path / "dir").mkdir()
    (tmp_path / "linked-dir").symlink_to(tmp_path / "dir")
    os.mkfifo(tmp_path / "fifo")
    for inputs in [["../file"], ["/file"], ["file", "file"], ["link"], ["dir"],
                   ["linked-dir/file"], ["fifo"], ["*.py"], ["a\\b"], ["a//b"]]:
        with pytest.raises(RunConfigurationError) as error:
            collect_source(inputs, tmp_path)
        assert "secret" not in str(error.value)
    with pytest.raises(RunConfigurationError):
        collect_source("file", tmp_path)


def test_total_size_and_file_count_limits(tmp_path):
    (tmp_path / "a").write_bytes(b"abc")
    (tmp_path / "b").write_bytes(b"def")
    with pytest.raises(RunConfigurationError):
        collect_source(["a", "b"], tmp_path, limit=5)
    with pytest.raises(RunConfigurationError):
        collect_source([str(i) for i in range(101)], tmp_path)
