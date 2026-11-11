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
    monkeypatch.setattr(SourceWorkspace, 'write', lambda *args: writes.append(args[1:]))
    def api(self, method, suffix, *, payload=None, **kwargs):
        if suffix == '/visualization-session':
            return {}
        return {'mode': 's3-presigned-batch', 'files': [{**f, 'canonicalPath': payload['targetPrefix'] + '/' + f['relativePath'], 'transfer': {'kind': 'single-put', 'put': {'url': 'https://storage.test/file', 'headers': {}}}} for f in payload['files']]}
    monkeypatch.setattr(SourceWorkspace, '_api', api)
    with pytest.raises(ValueError, match='upload failed'):
        publish_visualization(None, 'team', '1' * 24, tmp_path, storage_transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    assert writes == []
    result = publish_visualization(None, 'team', '1' * 24, tmp_path, storage_transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    assert result['files'] == 1
    assert writes[0][2] == '"baseline"'
    assert yaml.safe_load(writes[0][1])['dataset'] == {'format': 'folder'}
