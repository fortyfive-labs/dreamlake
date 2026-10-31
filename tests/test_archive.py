"""Archive wire contract and saved-edit baseline preservation."""
import json

import httpx
import pytest

import dreamlake as dl
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.notes import Note, NoteBusy, NoteReadOnly, NoteNotFound

NOTE = "507f1f77bcf86cd799439011"
STAMP = "2026-10-30T12:00:00.000Z"


def client(handler):
    return DreamLakeClient(dl_url="https://example.test", token="test",
                           transport=httpx.MockTransport(handler))


def test_archive_is_metadata_only_and_preserves_original_edit_baseline():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.path == (f"/notes/{NOTE}" if req.method == "GET" else f"/namespaces/ns/notes/{NOTE}")
        assert "if-match" not in req.headers
        archived = json.loads(req.content)["archived"] if req.method == "PATCH" else True
        return httpx.Response(200, json={"id": NOTE, "slug": "draft", "archivedAt": STAMP if archived else None})
    note = Note(NOTE, namespace="ns", client=client(handler))
    note._text, note._etag, note._text_snapshot = "unsaved draft", "original", object()
    original = (note._text, note._etag, note._text_snapshot)
    assert note.archive().archived_at == STAMP
    assert note.metadata().archived_at == STAMP
    assert note.unarchive().archived_at is None
    assert (note._text, note._etag, note._text_snapshot) == original
    assert [r.method for r in calls] == ["PATCH", "GET", "PATCH"]
    assert json.loads(calls[0].content) == {"archived": True}
    assert json.loads(calls[2].content) == {"archived": False}


@pytest.mark.parametrize("status,error", [(409, NoteBusy), (503, NoteBusy), (403, NoteReadOnly), (404, NoteNotFound)])
def test_archive_failure_never_retries_or_changes_draft(status, error):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, json={"error": "refused"})
    note = Note(NOTE, namespace="ns", client=client(handler))
    note._text, note._etag = "unsaved", "original"
    with pytest.raises(error):
        note.archive()
    assert (note._text, note._etag) == ("unsaved", "original")
    assert len(calls) == 1


@pytest.mark.parametrize("include", [False, True])
@pytest.mark.parametrize("search", [False, True])
def test_catalog_and_search_preserve_filter_and_legacy_metadata(include, search):
    def handler(req):
        assert req.url.params["includeArchived"] == str(include).lower()
        assert req.url.params.get("q") == ("draft" if search else None)
        return httpx.Response(200, json={"notes": [{"id": NOTE, "archivedAt": STAMP}, {"id": "legacy"}]})
    c = client(handler)
    refs = dl.search_notes("draft", namespace="ns", include_archived=include, client=c) if search else dl.list_notes("ns", include_archived=include, client=c)
    assert [r.archived_at for r in refs] == [STAMP, None]


def test_archived_slug_resolves_beyond_first_catalog_page_without_restoring():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.method == "GET"
        assert req.url.params["includeArchived"] == "true"
        if req.url.params["offset"] == "0":
            return httpx.Response(200, json={"notes": [{"id": "a", "slug": "other"}], "total": 2})
        assert req.url.params["offset"] == "1"
        return httpx.Response(200, json={"notes": [{"id": NOTE, "slug": "archived", "archivedAt": STAMP}], "total": 2})
    assert dl.note("ns/archived", client=client(handler)).id == NOTE
    assert len(calls) == 2


@pytest.mark.parametrize("archived", [True, False])
def test_folder_state_is_separate_patch_and_returns_server_row(archived):
    def handler(req):
        assert req.method == "PATCH" and req.url.path == f"/nodes/{NOTE}"
        assert json.loads(req.content) == {"archived": archived}
        return httpx.Response(200, json={"id": NOTE, "archivedAt": STAMP if archived else None})
    c = client(handler)
    result = c.archive_folder(NOTE) if archived else c.unarchive_folder(NOTE)
    assert result["archivedAt"] == (STAMP if archived else None)


def test_folder_rejection_propagates_without_retry():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(400, json={"error": "only-folders"})
    with pytest.raises(httpx.HTTPStatusError):
        client(handler).archive_folder(NOTE)
    assert len(calls) == 1


@pytest.mark.parametrize("include", [True, False])
def test_resource_and_children_queries_preserve_server_counts_and_cursor(include):
    calls = []
    response = {"nodes": [], "resources": [], "total": 0, "nextCursor": "next"}
    def handler(req):
        calls.append(req)
        assert req.url.params["includeArchived"] == str(include).lower()
        return httpx.Response(200, json=response)
    c = client(handler)
    assert c.get_node_children(NOTE, include_archived=include, recursive=True, search="draft", after="previous", page_size=12) == response
    assert c.projects.resources("ns", "project", include_archived=include, bindr="collection", page=2, page_size=12) == response
    assert calls[0].url.params["after"] == "previous"
    assert calls[0].url.params["recursive"] == "true"
    assert calls[1].url.path == "/namespaces/ns/projects/project/resources"
    assert calls[1].url.params["bindr"] == "collection"
    assert calls[1].url.params["page"] == "2"


def test_children_require_explicit_scope_and_recursive_search():
    c = client(lambda req: pytest.fail("invalid input reached network"))
    with pytest.raises(ValueError):
        c.get_node_children()
    with pytest.raises(ValueError):
        c.get_node_children(NOTE, recursive=True)


@pytest.mark.parametrize("include", [False, True])
def test_grep_cursor_shared_and_descendants_keep_archive_filter(include):
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.params["includeArchived"] == str(include).lower()
        if req.url.path.endswith("/notes/search"):
            if not req.url.params.get("cursor"):
                return httpx.Response(200, json={"hits": [], "nextCursor": "page-two"})
            assert req.url.params["cursor"] == "page-two"
            return httpx.Response(200, json={"hits": [], "nextCursor": None})
        return httpx.Response(200, json={"notes": [{"id": NOTE, "archivedAt": STAMP}], "descendants": [], "total": 0})
    c = client(handler)
    assert dl.grep_notes("query", namespace="ns", include_archived=include, client=c).next_cursor is None
    assert dl.shared_with_me(include_archived=include, client=c)[0].archived_at == STAMP
    assert c.get_node_descendants(NOTE, include_archived=include)["total"] == 0
    assert len(calls) == 4


@pytest.mark.parametrize("bindr", [False, True])
def test_complete_downloads_include_archived_descendants(monkeypatch, tmp_path, bindr):
    from types import SimpleNamespace
    calls = []
    downloaded = []
    def descendants(node_id, **options):
        calls.append((node_id, options))
        if not options.get("include_archived"):
            pytest.fail("complete download silently excluded archived paths")
        return {"root": {"id": NOTE, "name": "Archive", "kind": "folder"},
                "rootPrefix": ",root,", "descendants": [
                    {"id": "file", "name": "retained.txt", "path": ",root,"}]}
    c = SimpleNamespace(get_node_descendants=descendants,
                        get_bindr=lambda *args: {"id": "bindr", "name": "collection", "members": [NOTE]})
    monkeypatch.setattr(dl, "_get_client", lambda: c)
    monkeypatch.setattr(dl, "_download_file", lambda client, node_id, path: downloaded.append(node_id))
    if bindr:
        dl.download_bindr("collection", project="project@ns", to=str(tmp_path))
    else:
        dl.download(NOTE, to=str(tmp_path))
    assert downloaded == ["file"]
    assert all(options["include_archived"] for _, options in calls)
