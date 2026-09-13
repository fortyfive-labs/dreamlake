import json
import httpx
import pytest
from dreamlake.vault import Vault, VaultError, VaultWriteError


def test_policy_preview_activation_recovery_and_redaction():
    receipt = dict(prefix='alice/work', provider='aws-kms', keyRef='research', requestId='persisted-1', state='committed', committedAt='2030-01-01T00:00:00.000Z', secret='MUST-NOT-RETURN')
    observed = []
    def handler(req):
        observed.append(req)
        if req.method == 'PUT':
            assert req.headers['idempotency-key'] == 'persisted-1'
            assert json.loads(req.content) == dict(prefix='alice/work', keyRef='research')
            raise httpx.ReadError('sensitive provider diagnostic')
        if '/operations/' in req.url.path:
            return httpx.Response(200, json=receipt)
        return httpx.Response(200, json=dict(prefix='alice/work', provider='aws-kms', keyRef='managed', governingPrefix=None, overlappingPrefixes=[], hasEntries=False, hasWriteReceipts=False, hasHotpReceipts=False, selectedKeyRef='research', readiness='not-probed', canActivate=True, secret='MUST-NOT-RETURN'))
    vault = Vault(httpx.Client(base_url='http://fixture', transport=httpx.MockTransport(handler)))
    assert vault.kms.preview(prefix='alice/work/', key_ref='research')['canActivate'] is True
    with pytest.raises(VaultWriteError) as error:
        vault.kms.activate(prefix='alice/work', key_ref='research', request_id='persisted-1')
    assert error.value.request_id == 'persisted-1'
    assert 'sensitive' not in str(error.value)
    assert 'secret' not in vault.kms.status(request_id='persisted-1')
    assert 'secret' not in vault.kms.show(prefix='alice/work')
    before = len(observed)
    for args in [dict(prefix='alice/work', key_ref='arn:aws:attacker', request_id='id'), dict(prefix='../bad', key_ref='research', request_id='id'), dict(prefix='alice/work', key_ref='research', request_id='bad/id')]:
        with pytest.raises(VaultError): vault.kms.activate(**args)
    assert len(observed) == before


def test_wrong_receipt_and_server_failure_preserve_original_intent():
    def handler(req):
        return httpx.Response(200, json=dict(prefix='alice/wrong', provider='aws-kms', keyRef='research', requestId='id', state='committed', committedAt='2030-01-01T00:00:00.000Z'))
    vault = Vault(httpx.Client(base_url='http://fixture', transport=httpx.MockTransport(handler)))
    with pytest.raises(VaultWriteError) as error: vault.kms.activate(prefix='alice/work', key_ref='research', request_id='id')
    assert error.value.outcome == 'unknown'
