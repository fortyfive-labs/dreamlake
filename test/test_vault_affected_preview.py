import copy
import json
from pathlib import Path
import httpx
import pytest
from dreamlake.vault import Vault, VaultError
from dreamlake.vault_affected_preview import affected_options, affected_view

VECTORS = json.loads((Path(__file__).parent / 'fixtures/affected-preview-vectors.json').read_text())


def test_shared_metadata_vectors_and_projection():
    for vector in VECTORS:
        dirty = copy.deepcopy(vector['value'])
        dirty['secret'] = 'NEVER'
        dirty['entries'][0]['ciphertext'] = 'NEVER'
        dirty['entries'][0]['currentPolicy']['secret'] = 'NEVER'
        assert affected_view(dirty, vector['prefix'], vector['keyRef']) == vector['value']


@pytest.mark.parametrize('field,value', [('observedAt', 'bad'), ('contextFingerprint', 'bad'), ('schemaVersion', 2), ('nextCursor', '../bad'), ('blockedReason', 'active-migration')])
def test_invalid_page(field, value):
    page = copy.deepcopy(VECTORS[0]['value']); page[field] = value
    with pytest.raises(VaultError): affected_view(page, 'alice/project', 'selected')


@pytest.mark.parametrize('field,value', [('name', 'bob/secret'), ('revision', True), ('revision', 0), ('deleteAt', 'not-a-date'), ('proposedPolicy', dict(prefix='alice/project', keyRef='wrong', provider='aws-kms')), ('currentPolicy', dict(prefix='bob', keyRef='managed', provider='aws-kms', epoch=None))])
def test_invalid_row(field, value):
    page = copy.deepcopy(VECTORS[0]['value']); page['entries'][0][field] = value
    with pytest.raises(VaultError): affected_view(page, 'alice/project', 'selected')


def test_explicit_opt_in_request_and_old_server_refusal():
    calls = []
    response = dict(prefix='alice/project', provider='aws-kms', governingPrefix=None, keyRef='managed', overlappingPrefixes=[], selectedKeyRef='selected', readiness='not-probed')
    def handler(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, json=response)
    vault = Vault(httpx.Client(base_url='http://fixture', transport=httpx.MockTransport(handler)))
    vault.kms.preview(prefix='alice/project', key_ref='selected')
    assert calls[-1] == dict(prefix='alice/project', keyRef='selected')
    with pytest.raises(VaultError): vault.kms.preview(prefix='alice/project', key_ref='selected', affected_limit=2, affected_cursor='opaque')
    assert calls[-1]['affectedEntries'] == dict(limit=2, cursor='opaque')
    before = len(calls)
    for limit, cursor in [(None, 'opaque'), (0, None), (201, None), (True, None), ('2', None), (2, '../bad')]:
        with pytest.raises(VaultError): vault.kms.preview(prefix='alice/project', key_ref='selected', affected_limit=limit, affected_cursor=cursor)
    assert len(calls) == before
    response['affectedEntries'] = VECTORS[0]['value']
    assert vault.kms.preview(prefix='alice/project', key_ref='selected', affected_limit=1)['affectedEntries'] == VECTORS[0]['value']
