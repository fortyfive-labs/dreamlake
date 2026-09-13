"""Exercise actual main/CP/nymph/uv services; no simulated executor.

Run from this checkout with --env-file <protected fixture JSON> --target ns/group/host.
The fixture must supply baseUrl, token, outsiderToken, namespace, and an online
process host with uv. This client does not start services or change live databases.
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dreamlake import DreamLakeClient
from dreamlake.api.runs import RunConflictError, RunError


def logs(client, ns, run_id):
    cursor = None
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    for _ in range(1000):
        page = client.runs.logs(ns, run_id, cursor=cursor, limit=7)
        replay = client.runs.logs(ns, run_id, cursor=cursor, limit=7)
        assert replay == page, "cursor replay changed"
        for entry in page["entries"]:
            streams[entry["stream"]].extend(base64.b64decode(entry["dataBase64"], validate=True))
        cursor = page["nextCursor"]
        if page["done"]:
            return {key: bytes(value) for key, value in streams.items()}
    raise AssertionError("logs did not finish")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--target")
    parser.add_argument("--nymph", help="Build path; enroll a disposable worker through local SSH/systemd adapters")
    args = parser.parse_args()
    config = json.loads(Path(args.env_file).read_text())
    url = config.get("baseUrl", config.get("remote"))
    if urlparse(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("This integration harness requires an isolated loopback API fixture")
    client = DreamLakeClient(dl_url=url, token=config["token"])
    outsider = DreamLakeClient(dl_url=url, token=config["outsiderToken"])
    fixture_home = None
    original_env = dict(os.environ)
    unit = None
    target = args.target
    if args.nymph:
        fixture_home = tempfile.TemporaryDirectory(prefix="python-run-worker-")
        home = Path(fixture_home.name)
        bindir = home / "bin"
        bindir.mkdir()
        for filename in ["ssh", "systemctl", "loginctl"]:
            shutil.copyfile(Path(__file__).parent / "fixtures" / "hosts" / filename, bindir / filename)
            (bindir / filename).chmod(0o700)
        (bindir / "nymph").symlink_to(Path(args.nymph).resolve())
        os.environ["HOME"] = str(home)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ["PATH"]
        target = target or config["namespace"] + "/python-runs/host-" + uuid.uuid4().hex[:12]
        try:
            enrolled = client.hosts.enroll(target, ssh="fixture-host", lakeshore_id=config["lakeshoreId"], wait_seconds=60)
            assert enrolled["enrolled"], "fixture host did not become online"
            unit = next((home / ".config/systemd/user").glob("*.service")).name
        except BaseException:
            for entry in (home / ".config/systemd/user").glob("*.service"):
                subprocess.run([str(bindir / "systemctl"), "--user", "stop", entry.name], check=False)
            os.environ.clear()
            os.environ.update(original_env)
            fixture_home.cleanup()
            raise
    if not target:
        parser.error("Supply --target for an existing fixture host, or --nymph for a disposable worker")
    ns = target.split("/")[0]
    request = "python-run-" + str(uuid.uuid4())
    pending = []
    try:
        with tempfile.TemporaryDirectory(prefix="dreamlake-run-source-") as directory:
            cwd = Path(directory)
            (cwd / "echo.py").write_text("import json,sys\nprint(json.dumps(sys.argv[1:]))\nprint('stderr-π',file=sys.stderr)\n")
            argv = ["--no-project", "echo.py", "a b", "", "--json", "--uvx", "$(literal)", "π"]
            params = {"kind": "uv-run", "argv": argv, "include": ["echo.py"], "cwd": cwd, "request_id": request}
            run = client.runs.submit(target, **params)
            pending.append(run["id"])
            duplicate = client.runs.submit(target, **params)
            assert duplicate["id"] == run["id"], "duplicate submission created another run"
            finished = client.runs.wait(ns, run["id"], timeout_seconds=60)
            assert finished["status"] == "succeeded" and finished["exitCode"] == 0
            output = logs(client, ns, run["id"])
            assert json.loads(output["stdout"]) == argv[2:]
            assert "stderr-π" in output["stderr"].decode()
            try:
                client.runs.submit(target, **{**params, "argv": ["echo.py", "changed"]})
                raise AssertionError("changed replay accepted")
            except RunConflictError:
                pass
            try:
                outsider.runs.status(ns, run["id"])
                raise AssertionError("outsider read accepted")
            except RunError as error:
                assert error.status in {403, 404}
            (cwd / "fail.py").write_text("import sys\nprint('expected-failure',file=sys.stderr)\nsys.exit(7)\n")
            failed = client.runs.submit(target, kind="uv-run", argv=["--no-project", "fail.py"], include=["fail.py"], cwd=cwd)
            pending.append(failed["id"])
            terminal = client.runs.wait(ns, failed["id"], timeout_seconds=60)
            assert terminal["status"] == "failed" and terminal["exitCode"] == 7
            assert b"expected-failure" in logs(client, ns, failed["id"])["stderr"]
            (cwd / "slow.py").write_text("import time\nprint('started',flush=True)\ntime.sleep(60)\n")
            slow = client.runs.submit(target, kind="uv-run", argv=["--no-project", "slow.py"], include=["slow.py"], cwd=cwd)
            pending.append(slow["id"])
            for _ in range(30):
                state = client.runs.status(ns, slow["id"])["status"]
                if state == "running":
                    break
                assert state in {"queued", "running"}, "unexpected pre-cancel terminal state"
                time.sleep(0.5)
            client.runs.cancel(ns, slow["id"])
            stopped = client.runs.wait(ns, slow["id"], timeout_seconds=60)
            assert stopped["status"] == "cancelled", "cancel intent did not become terminal cancellation"
            timed = client.runs.submit(target, kind="uv-run", argv=["--no-project", "slow.py"], include=["slow.py"], cwd=cwd, timeout_seconds=2)
            pending.append(timed["id"])
            assert client.runs.wait(ns, timed["id"], timeout_seconds=60)["status"] == "timed_out"
            tool = client.runs.submit(target, kind="uvx", argv=["--from", "ruff", "ruff", "--version"], cwd=cwd)
            pending.append(tool["id"])
            assert client.runs.wait(ns, tool["id"], timeout_seconds=90)["status"] == "succeeded"
        print(json.dumps({"success": True, "nonGitSource": True, "argvPreserved": True,
                          "cursorReplay": True, "duplicateStable": True, "conflictRejected": True,
                          "outsiderDenied": True, "failedExit": 7, "cancelled": True, "timeout": True, "uvx": True}))
    finally:
        for run_id in pending:
            try:
                if client.runs.status(ns, run_id)["status"] in {"queued", "running", "cancel_requested"}:
                    client.runs.cancel(ns, run_id)
            except RunError:
                print("Fixture cleanup could not confirm cancellation; inspect pending runs")
        if unit:
            subprocess.run([str(bindir / "systemctl"), "--user", "stop", unit], check=False)
        os.environ.clear()
        os.environ.update(original_env)
        if fixture_home:
            fixture_home.cleanup()


if __name__ == "__main__":
    main()
