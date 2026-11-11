import httpx
import pytest
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.annotations import AnnotationMetadataError

ROW = {"id": "0123456789abcdef01234567", "name": "storage-name", "slug": "old", "slugRevision": 3, "title": "Retail Task Annotation"}


def client(handler):
    return DreamLakeClient(dl_url="https://example.test", token="test", transport=httpx.MockTransport(handler)).annotations


def test_guarded_rename_keeps_title_and_storage_name():
    requests = []
    def handler(request):
        import json
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=ROW)
        assert json.loads(request.content) == {"slug": "new", "expectedResourceId": ROW["id"], "expectedSlug": "old", "expectedRevision": 3}
        return httpx.Response(200, json={**ROW, "slug": "new", "slugRevision": 4})
    row = client(handler).rename("team", "old", "new")
    assert row["name"] == "storage-name" and row["title"] == ROW["title"]
    assert len(requests) == 2


@pytest.mark.parametrize("status", [403, 409])
def test_errors_are_not_retried(status):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "DENIED"})
    with pytest.raises(AnnotationMetadataError) as error:
        client(handler).update("team", "old", title="New title")
    assert error.value.status == status and error.value.code == "DENIED"
    assert len(requests) == 1


def test_omitted_slug_and_unsupported_server():
    import json
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == "PATCH":
            assert json.loads(request.content) == {"title": "Only title"}
        return httpx.Response(200, json={"name": "legacy"})
    api = client(handler)
    api.update("team", "legacy", title="Only title")
    with pytest.raises(AnnotationMetadataError, match="ANNOTATION_SLUG_UNSUPPORTED"):
        api.rename("team", "legacy", "new")
    assert len(requests) == 2


@pytest.mark.parametrize("slug", ["UPPER", "a/b", " spaced", "a" * 65])
def test_invalid_slug_never_sends(slug):
    def handler(request):
        raise AssertionError("must not send")
    with pytest.raises(ValueError):
        client(handler).update("team", "old", slug=slug, expected_resource_id=ROW["id"], expected_slug="old", expected_revision=3)
