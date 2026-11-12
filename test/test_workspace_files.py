"""Protocol tests against mocked API and storage, not production acceptance."""
from contextlib import contextmanager
import httpx
import pytest
from dreamlake.api.workspace_files import SourceWorkspace, WorkspaceFileError


@pytest.fixture
def workspace():
    objects, calls = {}, []
    revision = [0]
    def api(request):
        import json
        body = json.loads(request.content) if request.content else {}
        calls.append(('api', request))
        if request.url.path.endswith('/managed-read-capabilities'):
            return httpx.Response(200, json={'method': 'GET', 'url': 'https://storage.test/' + body['path'], 'allowedHeaders': {}})
        if request.url.path.endswith('/upload-authorizations'):
            file = body['files'][0]
            headers = {'if-none-match': '*'} if file.get('ifNoneMatch') else {'if-match': file['ifMatch']}
            return httpx.Response(200, json={'mode': 's3-presigned-batch', 'files': [{**file, 'canonicalPath': file['relativePath'], 'transfer': {'kind': 'single-put', 'put': {'url': 'https://storage.test/' + file['relativePath'], 'headers': headers}}}]})
        return httpx.Response(200, json={'entries': [], 'nextCursor': 'next'})
    def storage(request):
        calls.append(('storage', request))
        assert 'authorization' not in request.headers
        key = request.url.path
        old = objects.get(key)
        if request.method == 'PUT':
            if (request.headers.get('if-none-match') == '*' and old) or (request.headers.get('if-match') and (not old or request.headers['if-match'] != old[1])):
                return httpx.Response(412)
            revision[0] += 1
            objects[key] = (request.content, '"rev%d"' % revision[0])
            return httpx.Response(200)
        if not old:
            return httpx.Response(404)
        return httpx.Response(200, content=old[0], headers={'etag': old[1]})
    class Client:
        @contextmanager
        def http(self):
            with httpx.Client(base_url='https://api.test', transport=httpx.MockTransport(api), headers={'authorization': 'Bearer test'}) as client:
                yield client
    return SourceWorkspace(Client(), 'marvin', 'a'*24, 'workspaces/test', storage_transport=httpx.MockTransport(storage)), objects, calls


def test_create_read_edit_and_stale_writer(workspace):
    fs, objects, calls = workspace
    first = fs.write('scene.json', '{"n":1}', expected_revision=None)
    assert fs.read('scene.json') == first
    updated = fs.replace('scene.json', '1', '2', expected_revision=first['revision'])
    assert updated['content'] == '{"n":2}'
    with pytest.raises(WorkspaceFileError, match='file_revision_conflict'):
        fs.write('scene.json', 'lost', expected_revision=first['revision'])
    with pytest.raises(WorkspaceFileError, match='file_revision_conflict'):
        fs.write('scene.json', 'lost', expected_revision=None)
    assert objects['/workspaces/test/scene.json'][0] == b'{"n":2}'
    assert sum(req.url.path.endswith('/managed-read-capabilities') for kind, req in calls if kind == 'api') == 8


def test_prefix_invalid_paths_and_ambiguous_edits(workspace):
    fs, objects, calls = workspace
    for path in ['../other', '/absolute', 'a//b', 'a\\b']:
        with pytest.raises(WorkspaceFileError, match='file_path_invalid'):
            fs.read(path)
    first = fs.write('scene', 'aaa', expected_revision=None)
    with pytest.raises(WorkspaceFileError, match='file_replace_match_not_unique'):
        fs.replace('scene', 'aa', 'b', expected_revision=first['revision'])
    assert fs.list()['nextCursor'] == 'next'
    with pytest.raises(WorkspaceFileError, match='file_text_too_large_or_binary'):
        fs.write('large', 'x' * (256 * 1024 + 1), expected_revision=None)


def test_binary_and_weak_revision_refused(workspace):
    fs, objects, _ = workspace
    objects['/workspaces/test/binary'] = (b'\xff', '"rev1"')
    with pytest.raises(WorkspaceFileError, match='file_text_too_large_or_binary'):
        fs.read('binary')
    objects['/workspaces/test/weak'] = (b'hello', 'W/"rev1"')
    with pytest.raises(WorkspaceFileError, match='file_revision_unavailable'):
        fs.read('weak')


def test_missing_file_and_empty_match_share_typescript_errors(workspace):
    fs, _, calls = workspace
    with pytest.raises(WorkspaceFileError, match='file_not_found'):
        fs.read('absent.xml')
    before = len(calls)
    with pytest.raises(WorkspaceFileError, match='file_replace_empty_match'):
        fs.replace('absent.xml', '', 'x', expected_revision='"rev1"')
    assert len(calls) == before


def test_binary_round_trip_and_text_guard(workspace):
    fs, _, _ = workspace
    payload = b'\x00\xff\x80\r\n' * 100000
    first = fs.write_bytes('mesh.stl', payload, expected_revision=None)
    assert fs.read_bytes('mesh.stl') == first
    with pytest.raises(WorkspaceFileError, match='file_text_too_large_or_binary'):
        fs.read('mesh.stl')
    with pytest.raises(WorkspaceFileError, match='file_revision_conflict'):
        fs.write_bytes('mesh.stl', b'no', expected_revision=None)


def test_explicit_project_endpoint_has_same_operation_shape():
    from dreamlake.api.workspace_files import workspace_files
    import json, base64
    objects = {}
    def api(request):
        assert request.url.path.startswith('/namespaces/marvin/projects/' + 'b'*24 + '/workspace-files')
        body = json.loads(request.content) if request.content else {}
        path = body.get('path') or request.url.params.get('path')
        if request.method == 'PUT':
            current = objects.get(path)
            if (current['revision'] if current else None) != body['expectedRevision']:
                return httpx.Response(409, json={'error': 'file_revision_conflict'})
            objects[path] = {'path': path, 'base64': body['base64'], 'revision': 'project:' + str(len(objects)+1)}
            return httpx.Response(200, json={'path': path, 'revision': objects[path]['revision']})
        return httpx.Response(200, json=objects[path])
    class Client:
        def http(self):
            return httpx.Client(base_url='https://api.test', transport=httpx.MockTransport(api))
    fs = workspace_files(Client(), {'kind':'project','namespace':'marvin','project_id':'b'*24,'prefix':'root'})
    created = fs.write('scene.xml', '<mujoco/>', expected_revision=None)
    assert fs.read('scene.xml') == created
    assert fs.write_bytes('image.png', b'\x00\xff', expected_revision=None)['bytes'] == b'\x00\xff'
    assert fs.read_bytes('image.png')['bytes'] == b'\x00\xff'
    with pytest.raises(WorkspaceFileError, match='file_revision_conflict'):
        fs.write('scene.xml', 'stale', expected_revision=None)
