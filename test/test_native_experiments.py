import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.experiments import ExperimentRequestError

RUN = "a" * 24


def test_native_create_log_archive_and_project_scope():
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        assert request.headers["authorization"] == "Bearer account-token"
        return httpx.Response(200, json={"experiment": {"id": RUN, "archivedAt": None}, "count": 1})

    experiments = DreamLakeClient(token="account-token", transport=httpx.MockTransport(handle)).experiments
    assert experiments.create_run("team", "research", "trial_1")["id"] == RUN
    assert experiments.log("team", "research", RUN,
                           [{"timestamp": "2026-10-31T00:00:00Z", "level": "info", "message": "started"}])["count"] == 1
    assert experiments.archive("team", "research", RUN, False)["id"] == RUN
    assert seen == [
        ("PUT", "/native-experiments/ns/team/projects/research/runs/trial_1", None),
        ("POST", f"/native-experiments/ns/team/projects/research/runs/{RUN}/logs",
         {"entries": [{"timestamp": "2026-10-31T00:00:00Z", "level": "info", "message": "started"}]}),
        ("PATCH", f"/native-experiments/ns/team/projects/research/runs/{RUN}", {"archived": False}),
    ]


def test_uncertain_write_is_not_retried_and_error_body_is_redacted():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(503, text="DO-NOT-ECHO")

    experiments = DreamLakeClient(token="account-token", transport=httpx.MockTransport(handle)).experiments
    with pytest.raises(ExperimentRequestError, match="HTTP 503") as caught:
        experiments.create_run("team", "research", "trial_1")
    assert caught.value.status == 503
    assert "DO-NOT-ECHO" not in str(caught.value)
    assert len(calls) == 1


def test_invalid_run_identity_never_reaches_network():
    experiments = DreamLakeClient(token="account-token", transport=httpx.MockTransport(
        lambda request: pytest.fail("unexpected network request"))).experiments
    with pytest.raises(ValueError):
        experiments.get_run("team", "research", "dash-snowflake-id")
