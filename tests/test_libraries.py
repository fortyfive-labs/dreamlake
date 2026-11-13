import base64
import hashlib
import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.libraries import LibraryError


def client(handler):
    return DreamLakeClient(dl_url='https://api.test', token='caller', transport=httpx.MockTransport(handler)).libraries


def test_search_batch_identity_and_partial_results():
    def handle(request):
        assert request.headers['authorization'] == 'Bearer caller'
        if request.url.path == '/library-search':
            assert request.url.params['libraries'] == 'n/a,n/b'
            assert request.url.params['offset'] == '20'
            return httpx.Response(200, json={'hits': [], 'total': 20})
        assert json.loads(request.content)['requests'][0]['id'] == 'one'
        return httpx.Response(200, json={'results': [{'id': 'one', 'ok': True, 'data': {}}, {'id': 'two', 'ok': False, 'error': {'status': 404}}]})
    libraries = client(handle)
    assert libraries.search(['n/a', 'n/b'], 'cup', offset=20)['hits'] == []
    assert libraries.batch([{'id': 'one', 'operation': 'search'}, {'id': 'two', 'operation': 'asset'}])['results'][1]['ok'] is False
    with pytest.raises(ValueError):
        libraries.batch([{'id': 'x'}, {'id': 'x'}])


def test_image_content_and_explicit_missing():
    libraries = client(lambda r: httpx.Response(200, json={'status': 'ready', 'purpose': 'thumbnail', 'data': base64.b64encode(b'png').decode()}))
    image = libraries.image('n', 'l', 'a')
    assert image['content'] == b'png' and 'data' not in image
    assert client(lambda r: httpx.Response(200, json={'status': 'no_image'})).image('n', 'l', 'a') == {'status': 'no_image'}
    with pytest.raises(LibraryError) as error:
        client(lambda r: httpx.Response(404, json={})).image('n', 'private', 'a')
    assert error.value.status == 404


def test_download_dependencies_refreshes_signature_without_forwarding_auth(monkeypatch, tmp_path):
    files = [{'path': p, 'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest(), 'url': 'https://storage.test/' + p} for p in ['model.xml', 'meshes/a.obj']]
    reads = []
    libraries = client(lambda r: httpx.Response(200, json={'files': files, 'revision': 1, 'nextFileOffset': None}))
    # Patch storage transport only; caller API still uses its supplied MockTransport.
    original = httpx.Client
    def storage(request):
        assert 'authorization' not in request.headers
        reads.append(request.url.path)
        return httpx.Response(403 if len(reads) == 1 else 200, content=b'abc')
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(**({**kw, 'transport': httpx.MockTransport(storage)} if 'base_url' not in kw else kw)))
    paths = libraries.download('n', 'l', 'a', tmp_path)
    assert [p.relative_to(tmp_path).as_posix() for p in paths] == ['model.xml', 'meshes/a.obj']
    assert all(p.read_bytes() == b'abc' for p in paths)
    assert len(reads) == 3
    libraries.download('n', 'l', 'a', tmp_path)
    assert len(reads) == 3  # verified files are skipped


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'nested/../escape', 'a\\b'])
def test_download_rejects_traversal_before_storage(path, tmp_path):
    libraries = client(lambda r: httpx.Response(200, json={'files': [{'path': path, 'size': 3, 'url': 'https://storage.test/a'}]}))
    with pytest.raises(LibraryError, match='unsafe_library_path'):
        libraries.download('n', 'l', 'a', tmp_path)


def test_download_budget_and_symlink_protection(tmp_path):
    libraries = client(lambda r: httpx.Response(200, json={'files': [{'path': 'link/a', 'size': 3, 'url': 'https://storage.test/a'}]}))
    (tmp_path / 'link').symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(LibraryError, match='unsafe_library_path'):
        libraries.download('n', 'l', 'a', tmp_path)
