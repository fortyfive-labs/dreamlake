"""Bounded Source and Project text/byte workspaces with explicit revision guards."""
import re
import base64
from urllib.parse import quote, urlparse
import httpx

MAX_TEXT_BYTES = 256 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024


class WorkspaceFileError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _path(value, root=False):
    if not isinstance(value, str) or (not root and not value) or value.startswith('/') or value.endswith('/') or re.search(r'[\\\x00-\x1f\x7f-\x9f]', value) or len(value.encode()) > 1024 or any(p in {'.', '..'} or (not p and value) or len(p.encode()) > 255 for p in value.split('/')):
        raise WorkspaceFileError('file_path_invalid')
    return value


def _text(value):
    if not isinstance(value, str) or '\0' in value or len(value.encode()) > MAX_TEXT_BYTES:
        raise WorkspaceFileError('file_text_too_large_or_binary')
    return value


def _etag(value):
    return isinstance(value, str) and len(value) <= 1024 and re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]+"', value) is not None


class SourceWorkspace:
    """Explicit Source endpoint and prefix. Prefix narrows paths, not caller authority."""
    capabilities = {'durable': True, 'max_text_bytes': MAX_TEXT_BYTES, 'max_file_bytes': MAX_FILE_BYTES}

    def __init__(self, client, namespace, source_id, prefix, *, storage_transport=None):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', namespace) or not re.fullmatch(r'[a-fA-F0-9]{24}', source_id):
            raise WorkspaceFileError('file_endpoint_invalid')
        self.client, self.prefix, self.storage_transport = client, _path(prefix, True), storage_transport
        self.base = '/namespaces/' + quote(namespace, safe='') + '/sources/' + source_id

    def _resolve(self, path, root=False):
        _path(path, root)
        return self.prefix + ('/' + path if path else '') if self.prefix else path

    def _relative(self, path):
        _path(path, True)
        if not self.prefix:
            return path
        if path == self.prefix:
            return ''
        if not path.startswith(self.prefix + '/'):
            raise WorkspaceFileError('file_endpoint_outside_prefix')
        return path[len(self.prefix) + 1:]

    def _api(self, method, suffix, *, payload=None, params=None):
        with self.client.http() as http:
            response = http.request(method, self.base + suffix, json=payload, params=params)
            if not response.is_success:
                try:
                    code = response.json().get('error')
                except ValueError:
                    code = None
                raise WorkspaceFileError(code if isinstance(code, str) and code.startswith('file_') else 'file_api_' + str(response.status_code))
            return response.json()

    def list(self, path='', cursor=None):
        page = self._api('GET', '/browse', params={k: v for k, v in {'path': self._resolve(path, True), 'cursor': cursor, 'limit': 10}.items() if v is not None})
        return {'entries': [{'path': self._relative(e['path']), 'kind': e['kind'], 'size': e['size'], 'revision': None} for e in page['entries'] if e['origin']['kind'] == 'dreamlake-storage'], 'nextCursor': page['nextCursor']}

    def _storage(self, method, url, headers, content=None):
        target = urlparse(url)
        if target.scheme != 'https' or target.username or target.password or any(k.lower() in {'authorization', 'cookie'} for k in headers):
            raise WorkspaceFileError('file_capability_invalid')
        # Separate client: API bearer token is never forwarded to object storage.
        try:
            with httpx.Client(transport=self.storage_transport, follow_redirects=False, timeout=30) as http:
                with http.stream(method, url, headers=headers, content=content) as response:
                    if response.status_code in {409, 412}:
                        raise WorkspaceFileError('file_revision_conflict')
                    if method == 'GET' and response.status_code == 404:
                        raise WorkspaceFileError('file_not_found')
                    if not response.is_success:
                        raise WorkspaceFileError('file_write_unconfirmed' if method == 'PUT' else 'file_read_failed')
                    data = bytearray()
                    if method == 'GET':
                        for chunk in response.iter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_FILE_BYTES:
                                raise WorkspaceFileError('file_bytes_too_large')
                    return bytes(data), response.headers.get('etag')
        except httpx.HTTPError:
            raise WorkspaceFileError('file_write_unconfirmed' if method == 'PUT' else 'file_read_failed') from None

    def read(self, path):
        result = self.read_bytes(path)
        try:
            content = _text(result['bytes'].decode('utf-8'))
        except UnicodeDecodeError:
            raise WorkspaceFileError('file_text_too_large_or_binary') from None
        return {'path': path, 'content': content, 'revision': result['revision']}

    def read_bytes(self, path):
        resolved = self._resolve(path)
        cap = self._api('POST', '/managed-read-capabilities', payload={'path': resolved})
        if cap['method'] != 'GET':
            raise WorkspaceFileError('file_capability_invalid')
        data, revision = self._storage('GET', cap['url'], cap.get('allowedHeaders', {}))
        if not _etag(revision):
            raise WorkspaceFileError('file_revision_unavailable')
        self._api('POST', '/managed-read-capabilities', payload={'path': resolved})
        return {'path': path, 'bytes': data, 'revision': revision}

    def write(self, path, content, *, expected_revision):
        _text(content)
        result = self.write_bytes(path, content.encode(), expected_revision=expected_revision)
        return {'path': path, 'content': content, 'revision': result['revision']}

    def write_bytes(self, path, data, *, expected_revision):
        if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
            raise WorkspaceFileError('file_bytes_too_large')
        resolved = self._resolve(path)
        if expected_revision is not None and not _etag(expected_revision):
            raise WorkspaceFileError('file_revision_invalid')
        condition = {'ifNoneMatch': '*'} if expected_revision is None else {'ifMatch': expected_revision}
        size = len(data)
        plan = self._api('POST', '/upload-authorizations', payload={'files': [{'relativePath': resolved, 'size': size, 'mediaType': 'application/octet-stream', **condition}]})
        files = plan.get('files', [])
        if plan.get('mode') != 's3-presigned-batch' or len(files) != 1:
            raise WorkspaceFileError('file_capability_invalid')
        file = files[0]
        if file['relativePath'] != resolved or file['canonicalPath'] != resolved or file['size'] != size or file['transfer']['kind'] != 'single-put':
            raise WorkspaceFileError('file_capability_invalid')
        put = file['transfer']['put']
        headers = {k.lower(): v for k, v in put['headers'].items()}
        key, value = ('if-none-match', '*') if expected_revision is None else ('if-match', expected_revision)
        if headers.get(key) != value or ('if-match' in headers and 'if-none-match' in headers):
            raise WorkspaceFileError('file_precondition_missing')
        self._storage('PUT', put['url'], put['headers'], data)
        try:
            result = self.read_bytes(path)
            if result['bytes'] != data:
                raise WorkspaceFileError('file_write_unconfirmed')
            return result
        except Exception:
            raise WorkspaceFileError('file_write_unconfirmed') from None

    def replace(self, path, old_text, new_text, *, expected_revision):
        _text(old_text), _text(new_text)
        if not old_text:
            raise WorkspaceFileError('file_replace_empty_match')
        file = self.read(path)
        if file['revision'] != expected_revision:
            raise WorkspaceFileError('file_revision_conflict')
        at = file['content'].find(old_text)
        if not old_text or at < 0 or file['content'].find(old_text, at + 1) >= 0:
            raise WorkspaceFileError('file_replace_match_not_unique')
        return self.write(path, file['content'][:at] + new_text + file['content'][at + len(old_text):], expected_revision=expected_revision)


class ProjectWorkspace(SourceWorkspace):
    """Same relative operations, backed by native Project files rather than a Source mount."""
    def __init__(self, client, namespace, project_id, prefix):
        super().__init__(client, namespace, project_id, prefix)
        self.base = '/namespaces/' + quote(namespace, safe='') + '/projects/' + project_id + '/workspace-files'

    def list(self, path='', cursor=None):
        result = self._api('GET', '', params={k: v for k, v in {'path': self._resolve(path, True), 'cursor': cursor}.items() if v is not None})
        return {'entries': [{**e, 'path': self._relative(e['path'])} for e in result['entries']], 'nextCursor': result['nextCursor']}

    def read_bytes(self, path):
        result = self._api('GET', '/bytes', params={'path': self._resolve(path)})
        if self._relative(result['path']) != path:
            raise WorkspaceFileError('file_endpoint_path_mismatch')
        data = base64.b64decode(result['base64'], validate=True)
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceFileError('file_bytes_too_large')
        return {'path': path, 'bytes': data, 'revision': result['revision']}

    def write_bytes(self, path, data, *, expected_revision):
        if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
            raise WorkspaceFileError('file_bytes_too_large')
        if expected_revision is not None and (not isinstance(expected_revision, str) or not expected_revision):
            raise WorkspaceFileError('file_revision_required')
        result = self._api('PUT', '/bytes', payload={'path': self._resolve(path), 'base64': base64.b64encode(data).decode(), 'expectedRevision': expected_revision})
        if self._relative(result['path']) != path:
            raise WorkspaceFileError('file_endpoint_path_mismatch')
        return {'path': path, 'bytes': data, 'revision': result['revision']}


def workspace_files(client, endpoint, *, storage_transport=None):
    """Select an explicit endpoint; all operation arguments remain unchanged."""
    if endpoint.get('kind') == 'source':
        return SourceWorkspace(client, endpoint['namespace'], endpoint['source_id'], endpoint['prefix'], storage_transport=storage_transport)
    if endpoint.get('kind') == 'project':
        return ProjectWorkspace(client, endpoint['namespace'], endpoint['project_id'], endpoint['prefix'])
    raise WorkspaceFileError('file_endpoint_invalid')
