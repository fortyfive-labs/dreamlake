"""Saved URLs with explicit resource authority, bounded pages and guarded writes."""
import re
import secrets
from urllib.parse import quote

LINK_KINDS = frozenset(('project', 'bindr', 'node', 'source', 'note', 'artifact',
                       'annotation', 'workflow', 'env', 'repo', 'runnable', 'library', 'bundle', 'session'))


def new_link_id() -> str:
    """Generate and retain an identity before attempting a recoverable creation."""
    return secrets.token_hex(12)


def _id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{24}', value):
        raise ValueError('Use the immutable 24-hex resource or link ID')
    return value


def _version(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 2**53 - 1:
        raise ValueError('expected_version must come from a link read')
    return value


class SavedLinks:
    def __init__(self, client, namespace: str, kind: str, resource_id: str):
        if kind not in LINK_KINDS:
            raise ValueError('Unsupported saved link resource kind')
        if not isinstance(namespace, str) or not re.fullmatch(r'@?[A-Za-z0-9][A-Za-z0-9._-]{0,127}', namespace):
            raise ValueError('Use the owning namespace')
        self.client = client
        self.path = f'/namespaces/{quote(namespace.lstrip("@"), safe="")}/resources/{kind}/{_id(resource_id)}/links'

    def _request(self, method, suffix='', **kwargs):
        # No redirects or mutation retries. The caller retains the recovery ID/version.
        with self.client.http() as http:
            response = http.request(method, self.path + suffix, follow_redirects=False, **kwargs)
        response.raise_for_status()
        return None if response.status_code == 204 else response.json()

    def list(self, *, limit: int = 25, cursor: str | None = None) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit must be 1–100')
        return self._request('GET', params={'limit': limit, **({'cursor': _id(cursor)} if cursor is not None else {})})

    def get(self, link_id: str) -> dict:
        return self._request('GET', '/' + _id(link_id))

    def create(self, url: str, *, title: str = '', description: str = '', link_id: str) -> dict:
        """Use new_link_id() before sending; get that ID after an uncertain response."""
        return self._request('POST', json={'id': _id(link_id), 'url': url, 'title': title, 'description': description})

    def update(self, link_id: str, *, expected_version: int, **fields) -> dict:
        if not fields or set(fields) - {'url', 'title', 'description'}:
            raise ValueError('Supply url, title and/or description')
        return self._request('PATCH', '/' + _id(link_id), json={**fields, 'expectedVersion': _version(expected_version)})

    def remove(self, link_id: str, *, expected_version: int) -> None:
        self._request('DELETE', '/' + _id(link_id), params={'expectedVersion': _version(expected_version)})


def links(namespace: str, kind: str, resource_id: str) -> SavedLinks:
    """Open a resource's saved URLs with the configured default client."""
    from ._client import get_client
    return get_client().links(namespace, kind, resource_id)
