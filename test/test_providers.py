import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.providers import ProviderError, ProviderConfigurationError, ProviderAuthorizationError, ProviderConflictError, ProviderNotFound

ID = "a" * 24
OP = "b" * 24
DECLARATION = {"version": 1, "name": "bos14", "kind": "slurm-ssh", "config": {"cluster": "bos14", "partitions": ["gpu_a100"]}}


def client(handler):
    return DreamLakeClient(dl_url="https://providers.test", token="synthetic", transport=httpx.MockTransport(handler)).providers


def receipt(request_id="stable"):
    return {"operationId": OP, "requestId": request_id, "status": "committed", "resource": {"id": ID, "revision": 1}}


def test_registration_receipt_lookup_and_retirement_share_the_protocol():
    seen = []
    def handler(req):
        seen.append(req)
        assert req.headers["Authorization"] == "Bearer synthetic"
        return httpx.Response(200, json=receipt())
    providers = client(handler)
    assert providers.register("team", declaration=DECLARATION, request_id="stable") == receipt()
    assert json.loads(seen[0].content) == {"requestId": "stable", "declaration": DECLARATION}
    assert providers.operations.get("team", OP) == receipt()
    with pytest.raises(ProviderError):
        providers.operations.get("team", ID)
    assert providers.operations.find("team", "stable") == receipt()
    assert seen[-1].url.path.endswith("/by-request/stable")
    providers.status("team", ID, page=2, page_size=5)
    assert dict(seen[-1].url.params) == {"page": "2", "pageSize": "5"}
    providers.retire("team", ID, revision=1, request_id="stable", association_id=ID)
    assert seen[-1].url.path.endswith(f"/associations/{ID}/retire")
    assert json.loads(seen[-1].content) == {"requestId": "stable", "revision": 1}


@pytest.mark.parametrize("status,error", [(403, ProviderAuthorizationError), (409, ProviderConflictError), (404, ProviderNotFound), (503, ProviderError)])
def test_error_redaction_and_request_identity(status, error):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, json={"error": "DO-NOT-ECHO", "code": "request_id_conflict" if status == 409 else "DO-NOT-ECHO"})
    with pytest.raises(error) as caught:
        client(handler).register("team", declaration=DECLARATION, request_id="stable")
    assert caught.value.status == status and caught.value.request_id == "stable"
    assert "DO-NOT-ECHO" not in str(caught.value)
    assert len(calls) == 1


def test_transport_loss_and_malformed_receipt_preserve_request_id_without_retry():
    calls = []
    def lost(req):
        calls.append(req)
        raise httpx.ReadError("DO-NOT-ECHO")
    with pytest.raises(ProviderError) as caught:
        client(lost).register("team", declaration=DECLARATION, request_id="stable")
    assert len(calls) == 1 and caught.value.request_id == "stable"
    assert "DO-NOT-ECHO" not in str(caught.value)
    providers = client(lambda r: httpx.Response(200, json=receipt("different")))
    with pytest.raises(ProviderError):
        providers.register("team", declaration=DECLARATION, request_id="stable")
    with pytest.raises(ProviderError):
        providers.operations.find("team", "stable")


def test_local_validation_rejects_raw_secrets_and_bad_shapes_before_network():
    providers = client(lambda r: pytest.fail("unexpected network"))
    for declaration in [{**DECLARATION, "secret": "DO-NOT-UPLOAD"}, {**DECLARATION, "kind": []}, {**DECLARATION, "config": {"cluster": "bos14", "password": "DO-NOT-UPLOAD"}}]:
        with pytest.raises(ProviderConfigurationError):
            providers.register("team", declaration=declaration, request_id="stable")
    with pytest.raises(ProviderConfigurationError):
        providers.register("team/other", declaration=DECLARATION, request_id="stable")
    with pytest.raises(ProviderConfigurationError):
        providers.retire("team", ID, revision=True, request_id="stable")
    with pytest.raises(ProviderConfigurationError):
        providers.status("team", ID, page_size=101)


def test_explicit_check_returns_run_receipt_without_polling_or_retry():
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(202, json={"run": {"id": OP, "status": "queued", "purpose": "provider_check"}})
    providers = client(handler)
    run = providers.check("team", ID, association_id=OP, association_revision=1, request_id="probe")
    assert run["id"] == OP and len(seen) == 1
    assert seen[0].url.path == f"/namespaces/team/lakeshore-providers/{ID}/checks"
    assert json.loads(seen[0].content) == {"requestId": "probe", "associationId": OP, "associationRevision": 1}
    with pytest.raises(ProviderConfigurationError):
        providers.check("team", ID, association_id=OP, association_revision=True, request_id="bad")
    assert len(seen) == 1
    with pytest.raises(ProviderError):
        client(lambda req: httpx.Response(200, json=receipt())).check("team", ID, association_id=OP, association_revision=1, request_id="probe")
