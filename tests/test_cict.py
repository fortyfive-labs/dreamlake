import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.cict import CictError, CictNotFound

PROVIDER = "1" * 24
SOURCE = "2" * 24
POOL = "3" * 24
RUN = {
    "id": "91-4881",
    "jobId": 4881,
    "routing": {"kind": "matched", "pool": {"id": POOL, "name": "bos14-gpu"}},
    "evidence": {"state": "written", "sourceId": SOURCE, "prefix": "ci/acme/sim/2026-11-09/000000_supermimic_abcdef0_4881"},
}


def make(handler):
    return DreamLakeClient(dl_url="https://cict.test", token="synthetic", transport=httpx.MockTransport(handler)).cict


def server(seen):
    def handler(req):
        seen.append(req)
        if req.url.host == "storage.test":
            assert "Authorization" not in req.headers
            return httpx.Response(200, json={"verdict": "pass", "cases": []})
        assert req.headers["Authorization"] == "Bearer synthetic"
        path = req.url.path
        if path.endswith("/read-capabilities"):
            return httpx.Response(200, json={"url": "https://storage.test/r.json", "allowedHeaders": {}})
        if path.endswith("/installations/start"):
            return httpx.Response(200, json={"installUrl": "https://github.com/apps/x/installations/new?state=s"})
        if path.endswith("/installations"):
            return httpx.Response(200, json={"installations": []})
        if path.endswith("/runner-pools") and req.method == "POST":
            return httpx.Response(201, json={"id": POOL, **json.loads(req.content)})
        if req.method == "DELETE":
            return httpx.Response(200, json={"deleted": True})
        if path.endswith("/runner-pools"):
            return httpx.Response(200, json={"pools": []})
        if path.endswith("/github/jobs"):
            return httpx.Response(200, json={"jobs": []})
        if path.endswith("/cict/runs"):
            return httpx.Response(200, json={"runs": [RUN]})
        if path.endswith("/cict/runs/91-4881"):
            return httpx.Response(200, json={"run": RUN})
        return httpx.Response(404, json={"error": "cict_run_not_found", "code": "cict_run_not_found"})
    return handler


def test_routes_and_bodies():
    seen = []
    cict = make(server(seen))
    assert cict.github.connect("team").endswith("state=s")
    assert seen[-1].url.path == "/namespaces/team/github/installations/start"
    assert cict.github.installations("team") == []
    pool = cict.pools.create("team", name="bos14-gpu", provider_id=PROVIDER, evidence_source_id=SOURCE,
                             evidence_prefix="ci", env={"ASSETS": "/work/assets"})
    assert json.loads(seen[-1].content) == {
        "name": "bos14-gpu", "target": {"kind": "slurm", "providerId": PROVIDER},
        "evidence": {"sourceId": SOURCE, "prefix": "ci"}, "env": {"ASSETS": "/work/assets"},
    }
    assert pool["id"] == POOL
    assert cict.pools.list("team") == []
    assert cict.pools.delete("team", POOL) == {"deleted": True}
    assert cict.runs.list("team", limit=5)[0]["id"] == "91-4881"
    assert dict(seen[-1].url.params) == {"limit": "5"}
    assert cict.runs.get("team", "91-4881")["jobId"] == 4881
    assert cict.jobs("team") == []


def test_result_reads_through_a_capability_without_the_token():
    seen = []
    cict = make(server(seen))
    assert cict.runs.result("team", "91-4881") == {"verdict": "pass", "cases": []}
    capability = next(r for r in seen if r.url.path.endswith("/read-capabilities"))
    assert json.loads(capability.content) == {"path": RUN["evidence"]["prefix"] + "/result.json", "intent": "preview"}


def test_errors_and_refusals():
    seen = []
    cict = make(server(seen))
    with pytest.raises(CictNotFound) as error:
        cict.runs.get("team", "1-2")
    assert error.value.code == "cict_run_not_found"
    before = len(seen)
    for call in [
        lambda: cict.runs.get("team", "../x"),
        lambda: cict.runs.list("team", limit=500),
        lambda: cict.runs.result("team", "91-4881", file="../secret"),
        lambda: cict.pools.create("team", name="p", provider_id="nope"),
        lambda: cict.pools.create("team", name="p", provider_id=PROVIDER, env={"lower": "x"}),
        lambda: cict.pools.create("team", name="p", provider_id=PROVIDER, evidence_prefix="ci"),
        lambda: cict.pools.delete("a/b", POOL),
    ]:
        with pytest.raises(CictError):
            call()
    assert len(seen) == before


def test_pool_edits_platform_usage_and_admin():
    seen = []

    def handler(req):
        seen.append(req)
        path = req.url.path
        if path.endswith("/runner-targets"):
            return httpx.Response(200, json={"targets": [{"id": PROVIDER, "usable": True, "reason": None}]})
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": POOL, **json.loads(req.content)})
        if path.endswith("/cict/platform"):
            return httpx.Response(200, json={"available": True, "machines": [], "access": None})
        if path.endswith("/cict/usage"):
            return httpx.Response(200, json={"month": "2026-11", "gpuSeconds": 0, "jobs": 0, "byWorkflow": []})
        return httpx.Response(200, json={"ok": True})

    cict = make(handler)
    # Baseline comparisons were removed with the server's.
    assert not hasattr(cict.runs, "comparison")

    cict.pools.update("team", POOL, provider_id=PROVIDER, no_evidence=True, env=None)
    assert json.loads(seen[-1].content) == {"target": {"kind": "slurm", "providerId": PROVIDER}, "evidence": None, "env": None}
    cict.pools.update("team", POOL, evidence_source_id=SOURCE, evidence_prefix="ci", env={"A": "1"})
    assert json.loads(seen[-1].content) == {"evidence": {"sourceId": SOURCE, "prefix": "ci"}, "env": {"A": "1"}}
    assert cict.pools.targets("team")[0]["usable"] is True

    assert cict.platform.status("team")["available"] is True
    cict.platform.start("team")
    assert (seen[-1].method, seen[-1].url.path, json.loads(seen[-1].content)) == ("POST", "/namespaces/team/cict/platform/activate", {})
    cict.platform.set_source("team", SOURCE)
    assert (seen[-1].method, json.loads(seen[-1].content)) == ("PATCH", {"sourceId": SOURCE})
    cict.usage("team", month="2026-11")
    assert dict(seen[-1].url.params) == {"month": "2026-11"}

    cict.admin.set_namespace("robotics", state="allowed", max_concurrent=3)
    assert (seen[-1].method, seen[-1].url.path, json.loads(seen[-1].content)) == ("PUT", "/cict/platform/admin/namespaces/robotics", {"state": "allowed", "maxConcurrent": 3})
    cict.admin.put_machine("gpu-rtx5090", label="dreamlake-gpu-rtx5090", provider_id=PROVIDER, enabled=True, max_concurrent=6)
    assert json.loads(seen[-1].content) == {"label": "dreamlake-gpu-rtx5090", "providerId": PROVIDER, "enabled": True, "maxConcurrent": 6}
    cict.admin.overview()
    assert seen[-1].url.path == "/cict/platform/admin"

    before = len(seen)
    for call in [
        lambda: cict.pools.update("team", POOL),
        lambda: cict.pools.update("team", POOL, evidence_prefix="ci"),
        lambda: cict.pools.update("team", POOL, no_evidence=True, evidence_source_id=SOURCE),
        lambda: cict.platform.start("team", source_id="nope"),
        lambda: cict.usage("team", month="26-11"),
        lambda: cict.admin.set_namespace("robotics", state="active"),
        lambda: cict.admin.put_machine("gpu", color="red"),
    ]:
        with pytest.raises(CictError):
            call()
    assert len(seen) == before


def test_usage_detail_onboarding_and_admin_providers():
    seen = []

    def handler(req):
        seen.append(req)
        path = req.url.path
        if path.endswith("/cict/onboarding"):
            return httpx.Response(200, json={"ready": False, "route": "dreamlake-gpu", "next": {"key": "platform-started", "how": {"cli": "dl cict platform start team"}}})
        if path.endswith("/admin/providers"):
            return httpx.Response(200, json={"providers": [{"id": PROVIDER, "usable": True, "spec": {"gpus": 1}}]})
        return httpx.Response(200, json={"ok": True})

    cict = make(handler)
    cict.usage("team", month="2026-11", sender="ada", limit=5)
    assert dict(seen[-1].url.params) == {"month": "2026-11", "sender": "ada", "limit": "5"}
    status = cict.onboarding("team")
    assert (seen[-1].url.path, status["next"]["key"]) == ("/namespaces/team/cict/onboarding", "platform-started")
    assert cict.admin.providers()[0]["spec"] == {"gpus": 1}
    cict.admin.overview(month="2026-10")
    assert (seen[-1].url.path, dict(seen[-1].url.params)) == ("/cict/platform/admin", {"month": "2026-10"})

    before = len(seen)
    for call in [
        lambda: cict.usage("team", limit=0),
        lambda: cict.usage("team", sender="bad login!"),
        lambda: cict.admin.overview(month="2026-13"),
        lambda: cict.admin.put_machine("gpu", spec={"gpus": 1}),
    ]:
        with pytest.raises(CictError):
            call()
    assert len(seen) == before


def test_dispatch_runs_a_workflow_through_the_one_installation():
    seen = []

    def handler(req):
        seen.append(req)
        path = req.url.path
        if path.endswith("/github/installations"):
            return httpx.Response(200, json={"installations": [
                {"installationId": 91, "state": "active"}, {"installationId": 92, "state": "suspended"}]})
        if path.endswith("/cict/dispatch/branches"):
            assert req.url.params["repository"] == "acme/sim" and req.url.params["installationId"] == "91"
            return httpx.Response(200, json={"defaultBranch": "main", "branches": ["main"]})
        if path.endswith("/cict/dispatches") and req.method == "POST":
            body = json.loads(req.content)
            return httpx.Response(201, json={"dispatch": {**body, "state": "dispatched", "workflowRunId": 9001}})
        if path.endswith("/cict/dispatches"):
            return httpx.Response(200, json={"dispatches": []})
        return httpx.Response(404, json={"code": "not_found"})

    cict = make(handler)
    d = cict.dispatch("team", repository="acme/sim", workflow="cict.yml", inputs={"sim_seconds": "120"})
    assert d["state"] == "dispatched"
    body = json.loads(seen[-1].content)
    assert {k: body[k] for k in ("installationId", "repository", "workflow", "ref", "inputs")} == {
        "installationId": 91, "repository": "acme/sim", "workflow": "cict.yml", "ref": "main", "inputs": {"sim_seconds": "120"}}
    assert body["requestId"].startswith("py-")
    assert cict.dispatches("team", limit=5) == []
    assert seen[-1].url.params["limit"] == "5"

    count = len(seen)
    for bad in (dict(repository="acme"), dict(workflow="x.json"), dict(ref="a..b"), dict(ref="main.lock"), dict(inputs={"bad name": "x"}),
                dict(inputs={"n": 1}), dict(installation_id=0)):
        args = dict(repository="acme/sim", workflow="cict.yml", ref="main", installation_id=91) | bad
        with pytest.raises(CictError):
            cict.dispatch("team", **args)
    assert len(seen) == count, "malformed input is refused before any request"
