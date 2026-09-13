"""Exercise actual main/CP/nymph/uv services; no simulated executor.

Run from this checkout with --env-file <protected fixture JSON> --target ns/group/host.
The fixture must supply baseUrl, token, outsiderToken, namespace, and an online
process host with uv. This client does not start services or change live databases.
"""
import argparse
import base64
import json
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dreamlake import DreamLakeClient
from dreamlake.api.runs import RunAuthorizationError, RunConflictError, RunError


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
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.env_file).read_text())
    url = config["baseUrl"]
    if urlparse(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("This integration harness requires an isolated loopback API fixture")
    client = DreamLakeClient(dl_url=url, token=config["token"])
    outsider = DreamLakeClient(dl_url=url, token=config["outsiderToken"])
    ns = args.target.split("/")[0]
    request = "python-run-" + str(uuid.uuid4())
    pending = []
    try:
        with tempfile.TemporaryDirectory(prefix="dreamlake-run-source-") as directory:
            cwd = Path(directory)
            (cwd / "echo.py").write_text("import json,sys\nprint(json.dumps(sys.argv[1:]))\nprint('stderr-π',file=sys.stderr)\n")
            argv = ["echo.py", "a b", "", "--json", "--uvx", "$(literal)", "π"]
            params = {"kind": "uv-run", "argv": argv, "include": ["echo.py"], "cwd": cwd, "request_id": request}
            run = client.runs.submit(args.target, **params)
            pending.append(run["id"])
            duplicate = client.runs.submit(args.target, **params)
            assert duplicate["id"] == run["id"], "duplicate submission created another run"
            finished = client.runs.wait(ns, run["id"], timeout_seconds=60)
            assert finished["status"] == "succeeded" and finished["exitCode"] == 0
            output = logs(client, ns, run["id"])
            assert json.loads(output["stdout"]) == argv[1:]
            assert "stderr-π" in output["stderr"].decode()
            try:
                client.runs.submit(args.target, **{**params, "argv": ["echo.py", "changed"]})
                raise AssertionError("changed replay accepted")
            except RunConflictError:
                pass
            try:
                outsider.runs.status(ns, run["id"])
                raise AssertionError("outsider read accepted")
            except RunAuthorizationError:
                pass
            (cwd / "fail.py").write_text("import sys\nprint('expected-failure',file=sys.stderr)\nsys.exit(7)\n")
            failed = client.runs.submit(args.target, kind="uv-run", argv=["fail.py"], include=["fail.py"], cwd=cwd)
            pending.append(failed["id"])
            terminal = client.runs.wait(ns, failed["id"], timeout_seconds=60)
            assert terminal["status"] == "failed" and terminal["exitCode"] == 7
            assert b"expected-failure" in logs(client, ns, failed["id"])["stderr"]
            (cwd / "slow.py").write_text("import time\nprint('started',flush=True)\ntime.sleep(60)\n")
            slow = client.runs.submit(args.target, kind="uv-run", argv=["slow.py"], include=["slow.py"], cwd=cwd)
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
        print(json.dumps({"success": True, "nonGitSource": True, "argvPreserved": True,
                          "cursorReplay": True, "duplicateStable": True, "conflictRejected": True,
                          "outsiderDenied": True, "failedExit": 7, "cancelled": True}))
    finally:
        for run_id in pending:
            try:
                if client.runs.status(ns, run_id)["status"] in {"queued", "running", "cancel_requested"}:
                    client.runs.cancel(ns, run_id)
            except RunError:
                print("Fixture cleanup could not confirm cancellation; inspect pending runs")


if __name__ == "__main__":
    main()
