import httpx
import pytest
import yaml
from dreamlake.api.source_visualization import collect_visualization, publish_visualization
from dreamlake.api.workspace_files import SourceWorkspace


def test_collect(tmp_path):
    (tmp_path / 'index.html').write_text('hello')
    files, prefix = collect_visualization(tmp_path)
    assert prefix.startswith('.dreamlake/visualization/')
    assert collect_visualization(tmp_path)[1] == prefix
    assert files == [('index.html', b'hello')]
    (tmp_path / 'escape').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='symlink'):
        collect_visualization(tmp_path)


def test_publication_switches_only_after_upload(tmp_path, monkeypatch):
    (tmp_path / 'index.html').write_text('hello')
    writes = []
    monkeypatch.setattr(SourceWorkspace, 'read', lambda *args: {'content': 'dataset: {format: folder}', 'revision': '"baseline"'})
    monkeypatch.setattr(SourceWorkspace, 'write', lambda self, path, content, *, expected_revision: writes.append((path, content, expected_revision)))
    def api(self, method, suffix, *, payload=None, **kwargs):
        if suffix == '':
            return {'name': 'my data'}
        if suffix == '/visualization-session':
            return {}
        return {'mode': 's3-presigned-batch', 'files': [{**f, 'canonicalPath': payload['targetPrefix'] + '/' + f['relativePath'], 'transfer': {'kind': 'single-put', 'put': {'url': 'https://storage.test/file', 'headers': {}}}} for f in payload['files']]}
    monkeypatch.setattr(SourceWorkspace, '_api', api)
    with pytest.raises(ValueError, match='upload failed'):
        publish_visualization(None, 'team', '1' * 24, tmp_path, storage_transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    assert writes == []
    result = publish_visualization(None, 'team', '1' * 24, tmp_path, storage_transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    assert result['files'] == 1
    assert result['url'] == 'https://dreamlake.ai/team/sources/my%20data?view=visualize'
    assert writes[0][2] == '"baseline"'
    assert yaml.safe_load(writes[0][1])['dataset'] == {'format': 'folder'}


def test_publisher_uses_real_revision_guarded_workspace(tmp_path):
    import json
    from dreamlake import DreamLakeClient
    (tmp_path / 'index.html').write_text('<h1>app</h1>')
    state = {'content': 'metadata: {preserved: true}\n', 'revision': '"baseline"', 'writes': []}

    def api(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if request.method == 'GET':
            return httpx.Response(200, json={'name': 'test-data'})
        if path.endswith('/managed-read-capabilities'):
            return httpx.Response(200, json={'method': 'GET', 'url': 'https://storage.test/config', 'allowedHeaders': {}})
        if path.endswith('/visualization-session'):
            return httpx.Response(200, json={})
        assert path.endswith('/upload-authorizations')
        prefix = body.get('targetPrefix', '')
        grants = []
        for f in body['files']:
            config = f['relativePath'] == '.dreamrc'
            if config:
                assert f['ifMatch'] == '"baseline"'
                assert state['writes'] == ['code']
            grants.append({**f, 'canonicalPath': prefix + '/' + f['relativePath'] if prefix else f['relativePath'],
                'transfer': {'kind': 'single-put', 'put': {'url': 'https://storage.test/' + ('config' if config else 'code'),
                    'headers': {'If-Match': f['ifMatch']} if config else {}}}})
        return httpx.Response(201, json={'mode': 's3-presigned-batch', 'files': grants})

    def storage(request):
        if request.method == 'GET':
            return httpx.Response(200, content=state['content'], headers={'etag': state['revision']})
        if request.url.path == '/config':
            assert request.headers['if-match'] == '"baseline"'
            state['content'] = request.content.decode()
            state['revision'] = '"published"'
            state['writes'].append('config')
        else:
            state['writes'].append('code')
        return httpx.Response(200)

    client = DreamLakeClient(dl_url='https://api.test', token='test', transport=httpx.MockTransport(api))
    result = publish_visualization(client, 'team', '1' * 24, tmp_path, storage_transport=httpx.MockTransport(storage))
    assert state['writes'] == ['code', 'config']
    config = yaml.safe_load(state['content'])
    assert config['metadata'] == {'preserved': True}
    assert config['visualize']['entry'] == result['entry']
