"""Exercise SDK recovery-field compatibility over real loopback HTTP.

The server supplies synthetic contract responses; this is not CP/Nymph recovery
acceptance. No cloud credentials, worker, database or remote mutations are used.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from dreamlake import DreamLakeClient
from dreamlake.api.runs import RunWaitTimeout


def check_recovery_contract():
    observation = {
        "version": 1, "state": "reconciliation_required",
        "reason": "prior_boot_admission", "observedAt": "2026-09-15T00:00:00Z",
    }
    pending = {"id": "fixture-run", "status": "running", "cancelRequested": True,
               "recoveryObservation": observation, "recoveryRevision": 1}
    terminal = {**pending, "status": "succeeded", "exitCode": 0,
                "recoveryObservation": None, "recoveryRevision": 2}
    legacy = {"id": "fixture-run", "status": "failed", "exitCode": 7}
    responses = []
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append((self.command, self.path))
            if self.path != "/namespaces/fixture/runs/fixture-run" or self.headers.get("Authorization") != "Bearer fixture-only":
                self.send_error(403)
                return
            if not responses:
                self.send_error(500)
                return
            payload = json.dumps({"run": responses.pop(0)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            requests.append((self.command, self.path))
            self.send_error(405)  # Any implicit mutation is a test failure.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = DreamLakeClient(dl_url=f"http://127.0.0.1:{server.server_port}", token="fixture-only")
        responses.append(pending)
        assert client.runs.status("fixture", "fixture-run") == pending

        # An observation is not a terminal result and does not stop polling.
        responses.append(pending)
        try:
            client.runs.wait("fixture", "fixture-run", timeout_seconds=0)
        except RunWaitTimeout as error:
            assert error.run_id == "fixture-run"
        else:
            raise AssertionError("Recovery observation incorrectly became terminal")

        responses.extend([pending, terminal])
        assert client.runs.wait("fixture", "fixture-run", timeout_seconds=5, poll_seconds=.001) == terminal

        # Compatibility includes absence and unfamiliar additive metadata.
        responses.append(legacy)
        assert client.runs.wait("fixture", "fixture-run", timeout_seconds=0) == legacy
        future = {**pending, "recoveryObservation": {"version": 2, "future": "opaque"}, "recoveryRevision": 3}
        responses.append(future)
        assert client.runs.status("fixture", "fixture-run") == future
        assert not responses
        assert requests == [("GET", "/namespaces/fixture/runs/fixture-run")] * 6
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
    return {"requests": len(requests), "writes": 0, "cleanup": True}


if __name__ == "__main__":
    print("PASS real loopback HTTP SDK contract:", json.dumps(check_recovery_contract(), sort_keys=True))
