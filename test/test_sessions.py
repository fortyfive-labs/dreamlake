import httpx
import pytest
from dreamlake import DreamLakeClient
from dreamlake.api.sessions import SessionError

ID = "a" * 24


def test_registry_reads_preserve_identity_freshness_and_binding_without_execution():
    calls = []
    row = {
        "id": ID,
        "provider": "claude",
        "vendorSessionId": "native-uuid",
        "runState": "unknown",
        "lastRunState": "waiting",
        "attention": "needs_input",
        "archiveOnly": False,
        "enrollmentId": "enrollment",
        "username": "ubuntu",
        "claudeConfigDir": "/same-user/custom",
    }

    def handle(req):
        calls.append(req)
        assert req.method == "GET"
        assert req.headers["Authorization"] == "Bearer synthetic"
        if req.url.path.endswith("/metadata"):
            return httpx.Response(200, json={"session": row})
        if req.url.path.endswith("/attention"):
            return httpx.Response(
                200, json={"needsInput": 21, "error": 0, "sessions": [row]}
            )
        return httpx.Response(
            200, json={"sessions": [row], "hasMore": True, "nextCursor": "next"}
        )

    sessions = DreamLakeClient(
        dl_url="https://registry.test",
        token="synthetic",
        transport=httpx.MockTransport(handle),
    ).sessions
    page = sessions.search(
        "alice", "repair", search_in="title", provider="claude", page_size=2
    )
    assert page["hasMore"] and page["nextCursor"] == "next"
    assert dict(calls[-1].url.params) == {
        "q": "repair",
        "searchIn": "title",
        "provider": "claude",
        "pageSize": "2",
        "sort": "recent",
        "archived": "exclude",
    }
    assert len(calls) == 1  # caller owns pagination
    assert sessions.get("alice", ID) == row
    assert sessions.attention("alice")["needsInput"] == 21
    sessions.targets("alice")
    assert calls[-1].url.path == "/namespaces/alice/hosts/terminal-targets"
    sessions.target("alice", ID)
    assert calls[-1].url.path == f"/namespaces/alice/terminal-sessions/{ID}/target"
    sessions.list("alice", cursor="next")
    assert "searchIn" not in calls[-1].url.params
    sessions.available(owner="alice", q="repair", search_in="all")
    assert calls[-1].url.path == "/terminal-sessions/available"
    assert calls[-1].url.params["owner"] == "alice"
    assert sessions.reference("alice", ID) == f":session[alice/{ID}]"


@pytest.mark.parametrize(
    "filters",
    [
        {"search_in": "body"},
        {"page_size": True},
        {"page_size": 101},
        {"q": "x" * 201},
        {"machine_id": ""},
        {"provider": "other"},
    ],
)
def test_invalid_queries_fail_before_network(filters):
    def unexpected(req):
        pytest.fail("must not request")

    sessions = DreamLakeClient(transport=httpx.MockTransport(unexpected)).sessions
    with pytest.raises(ValueError):
        sessions.list("alice", **filters)
    with pytest.raises(ValueError):
        sessions.get("alice", "01a13cbc-d4a7-7893-81cc-39696769c454")


@pytest.mark.parametrize("status", [401, 403, 404, 409, 422, 503])
def test_denials_do_not_echo_bodies_retry_or_change_target(status):
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(status, json={"error": "private-body-never-echo"})

    sessions = DreamLakeClient(transport=httpx.MockTransport(handle)).sessions
    with pytest.raises(SessionError) as error:
        sessions.get("alice", ID)
    assert error.value.status == status
    assert "private-body" not in str(error.value)
    assert len(calls) == 1


def test_mismatched_identity_is_not_returned():
    sessions = DreamLakeClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"session": {"id": "b" * 24}})
        )
    ).sessions
    with pytest.raises(SessionError, match="session_invalid_response"):
        sessions.get("alice", ID)


def test_available_preserves_api_owner_selector():
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(200, json={"sessions": []})

    sessions = DreamLakeClient(transport=httpx.MockTransport(handle)).sessions
    sessions.available(owner="@alice.smith", team="research.team")
    assert dict(calls[0].url.params)["owner"] == "@alice.smith"
    assert dict(calls[0].url.params)["team"] == "research.team"
    with pytest.raises(ValueError):
        sessions.available(owner="alice/other")
