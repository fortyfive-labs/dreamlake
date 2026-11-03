import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.runnables import RunnableCreate, RunnableError


def client_with(handler):
    return DreamLakeClient(dl_url="https://fixture.test", token="synthetic",
                           transport=httpx.MockTransport(handler))


def test_inline_and_note_backed_agents_use_caller_identity_and_atomic_project_payloads():
    requests = []

    def serve(request):
        requests.append(request)
        return httpx.Response(201, json={"id": "agent-id", **json.loads(request.content)})

    client = client_with(serve)
    inline: RunnableCreate = {
        "name": "agents.research", "kind": "agent", "markdown": "Read the selected notes.",
        "metadata": {"agent": {"runtime": "builtin-v1", "tools": ["dreamlake_notes_read"]}},
        "projects": ["dreamlake"],
    }
    assert client.runnables.create("a/b", inline)["id"] == "agent-id"
    referenced: RunnableCreate = {
        "module": "agents", "qualname": "writer", "kind": "agent",
        "content": {"instructionNoteId": "000000000000000000000001", "skills": [
            {"kind": "note", "id": "000000000000000000000002"},
            {"kind": "folder", "id": "000000000000000000000003"},
        ]},
        "metadata": inline["metadata"], "projects": ["dreamlake"],
    }
    client.runnables.create("team", referenced)
    assert requests[0].url.raw_path == b"/namespaces/a%2Fb/runnables"
    assert json.loads(requests[0].content) == inline
    assert json.loads(requests[1].content) == referenced
    assert "markdown" not in json.loads(requests[1].content)
    assert all(r.method == "POST" and r.headers["authorization"] == "Bearer synthetic" for r in requests)


def test_list_get_update_archive_restore_and_delete_preserve_exact_wire_contracts():
    requests = []

    def serve(request):
        requests.append(request)
        return httpx.Response(200, json={"success": True, "id": "agent-id"})

    runnables = client_with(serve).runnables
    runnables.list("team", kind="agent", module="agents", include_archived=True, page=2, page_size=25)
    runnables.get("team", "agents/research")
    runnables.update("team", "agent-id", {"description": None, "expectedUpdatedAt": "2026-11-03T10:00:00.000Z"})
    runnables.archive("team", "agent-id", expected_updated_at="2026-11-03T10:00:01.000Z")
    runnables.restore("team", "agent-id")
    assert runnables.delete("team", "agent-id") == {"success": True, "id": "agent-id"}
    assert dict(requests[0].url.params) == {"kind": "agent", "module": "agents", "includeArchived": "true", "page": "2", "pageSize": "25"}
    assert requests[1].url.raw_path == b"/namespaces/team/runnables/agents%2Fresearch"
    assert json.loads(requests[2].content) == {"description": None, "expectedUpdatedAt": "2026-11-03T10:00:00.000Z"}
    assert json.loads(requests[3].content) == {"archived": True, "expectedUpdatedAt": "2026-11-03T10:00:01.000Z"}
    assert json.loads(requests[4].content) == {"archived": False}
    assert [r.method for r in requests] == ["GET", "GET", "PATCH", "PATCH", "PATCH", "DELETE"]


def test_archive_stays_archived_when_an_unrelated_field_is_edited():
    state = {"id": "agent-id", "archivedAt": None, "markdown": "Keep this"}

    def serve(request):
        payload = json.loads(request.content)
        if "archived" in payload:
            state["archivedAt"] = "2026-11-03T10:00:00.000Z" if payload.pop("archived") else None
        state.update(payload)
        return httpx.Response(200, json=state)

    runnables = client_with(serve).runnables
    archived = runnables.archive("team", "agent-id")
    edited = runnables.update("team", "agent-id", {"description": "New description"})
    assert edited["archivedAt"] == archived["archivedAt"]
    assert edited["markdown"] == "Keep this"
    assert runnables.restore("team", "agent-id")["archivedAt"] is None


@pytest.mark.parametrize("status,error", [(403, "Forbidden"), (404, "runnable not found"), (409, "Runnable changed; reload before saving")])
def test_server_denials_and_stale_saves_are_not_retried(status, error):
    calls = []

    def serve(request):
        calls.append(request)
        return httpx.Response(status, json={"error": error, "privateDetails": "never-echo"})

    with pytest.raises(RunnableError) as caught:
        client_with(serve).runnables.update("team", "agent-id", {"markdown": "Draft", "expectedUpdatedAt": "old"})
    assert caught.value.status == status
    assert caught.value.code == error
    assert "never-echo" not in str(caught.value)
    assert len(calls) == 1


def test_empty_namespace_or_identity_and_invalid_pagination_never_send():
    requests = []
    client = client_with(lambda request: requests.append(request))
    for action in [lambda: client.runnables.list(""), lambda: client.runnables.get("team", ""),
                   lambda: client.runnables.get("team", ".."), lambda: client.runnables.list(".."),
                   lambda: client.runnables.list("team", page=0), lambda: client.runnables.list("team", page_size=201)]:
        with pytest.raises(ValueError):
            action()
    assert requests == []


@pytest.mark.parametrize("response,code", [(httpx.Response(200, json=[]), "INVALID_RUNNABLE_RESPONSE"),
                                           (httpx.Response(200, text="invalid"), "INVALID_RUNNABLE_RESPONSE"),
                                           (httpx.Response(500, json=["private-details"]), "RUNNABLE_REQUEST_FAILED")])
def test_invalid_responses_are_structured_errors(response, code):
    with pytest.raises(RunnableError) as caught:
        client_with(lambda _: response).runnables.get("team", "agent-id")
    assert caught.value.code == code
    assert "private-details" not in str(caught.value)


def test_project_attach_and_detach_accept_runnable_database_ids():
    requests = []

    def serve(request):
        requests.append(request)
        return httpx.Response(200, json={"success": True})

    client = client_with(serve)
    resource_id = "000000000000000000000001"
    client.projects.attach("team", "research", "runnable", resource_id)
    client.projects.detach("team", "research", "runnable", resource_id)
    assert [r.method for r in requests] == ["POST", "DELETE"]
    assert all(json.loads(r.content) == {"resourceType": "runnable", "resourceId": resource_id} for r in requests)


def test_published_examples_execute_through_the_client_contract(monkeypatch):
    from pathlib import Path
    import re
    import dreamlake

    saved = {}
    requests = []

    def serve(request):
        requests.append(request)
        if request.method == "POST":
            item = {"id": f"agent-{len(saved) + 1}", "archivedAt": None,
                    "updatedAt": "2026-11-03T10:00:00.000Z", **json.loads(request.content)}
            saved[item["id"]] = item
            return httpx.Response(201, json=item)
        if request.url.path.endswith("/runnables"):
            return httpx.Response(200, json={"runnables": list(saved.values()), "page": 1, "pageSize": 50, "total": len(saved), "totalPages": 1})
        item = saved[request.url.path.rsplit("/", 1)[1]]
        if request.method == "PATCH":
            changes = json.loads(request.content)
            assert changes.pop("expectedUpdatedAt", item["updatedAt"]) == item["updatedAt"]
            if "archived" in changes:
                item["archivedAt"] = item["updatedAt"] if changes.pop("archived") else None
            item.update(changes)
        return httpx.Response(200, json=item)

    client = client_with(serve)
    monkeypatch.setattr(dreamlake, "DreamLakeClient", lambda: client)
    for key in ["INSTRUCTION_NOTE_ID", "STYLE_NOTE_ID", "SKILLS_FOLDER_ID"]:
        monkeypatch.setenv(key, "000000000000000000000001")
    document = Path(__file__).parents[1] / "docs/api/runnables.md"
    snippets = re.findall(r"```python\n(.*?)\n```", document.read_text(), re.DOTALL)
    assert len(snippets) == 3
    scope = {}
    for snippet in snippets:
        exec(compile(snippet, str(document), "exec"), scope)
    assert len(saved) == 2
    assert scope["restored"]["archivedAt"] is None
    assert all(request.url.host == "fixture.test" for request in requests)
