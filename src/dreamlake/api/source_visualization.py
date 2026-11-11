"""Publish static visualization code owned and authorized by one Source."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse
import httpx
import yaml
from .workspace_files import SourceWorkspace, WorkspaceFileError, _path


def collect_visualization(directory, entry='index.html'):
    root = Path(directory)
    _path(entry)
    if not re.search(r'\.html?$', entry) or re.search(r'[%?#]', entry):
        raise ValueError('Entry must be a relative HTML path')
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Supply a static build directory, not a symlink')
    files, total = [], 0
    def walk(folder):
        nonlocal total
        for item in sorted(folder.iterdir()):
            if item.is_symlink():
                raise ValueError('Visualization symlinks are not supported')
            if item.is_dir():
                walk(item)
                continue
            path = item.relative_to(root).as_posix()
            _path(path)
            if not item.is_file() or re.search(r'[%?#]', path) or path.startswith('_dreamlake/'):
                raise ValueError('Unsupported visualization asset')
            if item.stat().st_size > 32 * 1024**2 or len(files) >= 1000:
                raise ValueError('Visualization exceeds asset or file count limit')
            data = item.read_bytes()
            total += len(data)
            if len(data) > 32 * 1024**2 or total > 128 * 1024**2:
                raise ValueError('Visualization exceeds asset or bundle size limit')
            files.append((path, data))
    walk(root)
    if not any(path == entry for path, _ in files):
        raise ValueError('Visualization entry is missing')
    digest = hashlib.sha256()
    for path, data in files:
        digest.update(json.dumps([path, hashlib.sha256(data).hexdigest()], separators=(',', ':'), ensure_ascii=False).encode())
    return files, '.dreamlake/visualization/' + digest.hexdigest()


def publish_visualization(client, namespace, source_id, directory, *, entry='index.html', storage_transport=None):
    """Upload a new generation, then CAS the .dreamrc pointer. No Artifact or ACL is created."""
    files, prefix = collect_visualization(directory, entry)
    workspace = SourceWorkspace(client, namespace, source_id, '', storage_transport=storage_transport)
    text, revision = '', None
    try:
        baseline = workspace.read('.dreamrc')
        text, revision = baseline['content'], baseline['revision']
    except WorkspaceFileError as error:
        if error.code != 'file_not_found':
            raise
        if workspace._api('GET', '/landing')['dreamrc']['state'] != 'absent':
            raise ValueError('Materialize the resolved external .dreamrc in managed storage before publishing')
    config = yaml.safe_load(text) if text else {}
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError('Existing .dreamrc must be a YAML mapping')
    path = prefix + '/' + entry
    config['visualize'] = {'type': 'custom', 'entry': path}
    content = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    workspace._api('POST', '/visualization-session', payload={'entry': path})
    plan = workspace._api('POST', '/upload-authorizations', payload={
        'targetPrefix': prefix, 'files': [{'relativePath': p, 'size': len(data)} for p, data in files],
    })
    grants = plan.get('files', [])
    if plan.get('mode') != 's3-presigned-batch' or len(grants) != len(files):
        raise ValueError('Invalid upload plan')
    for (name, data), grant in zip(files, grants):
        if grant.get('relativePath') != name or grant.get('canonicalPath') != prefix + '/' + name or grant.get('size') != len(data) or grant.get('transfer', {}).get('kind') != 'single-put':
            raise ValueError('Invalid asset authorization')
        put = grant['transfer']['put']
        url = urlparse(put['url'])
        if url.scheme != 'https' or url.username or url.password or any(k.lower() in {'authorization', 'cookie'} for k in put.get('headers', {})):
            raise ValueError('Unsafe storage capability')
    with httpx.Client(transport=storage_transport, timeout=60, follow_redirects=False) as storage:
        for (_, data), grant in zip(files, grants):
            put = grant['transfer']['put']
            response = storage.put(put['url'], headers=put['headers'], content=data)
            if not response.is_success:
                raise ValueError('Visualization upload failed; .dreamrc was not changed')
    workspace.write('.dreamrc', content, revision)
    return {'namespace': namespace, 'sourceId': source_id, 'entry': path, 'files': len(files),
            'url': f'https://dreamlake.ai/{namespace}/sources/{source_id}?view=visualize'}
