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


@pytest.mark.parametrize("method,changes", [("show", {"prefix": "alice/other"}), ("preview", {"prefix": "alice/other"}), ("preview", {"selectedKeyRef": "different"})])
def test_read_metadata_must_match_requested_identity(method, changes):
    response = dict(prefix="alice/work", provider="aws-kms", keyRef="managed", governingPrefix=None, overlappingPrefixes=[], selectedKeyRef="research", readiness="not-probed")
    response.update(changes)
    vault = Vault(httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))))
    with pytest.raises(VaultError, match="Mismatched KMS"):
        if method == "show": vault.kms.show(prefix="alice/work/")
        else: vault.kms.preview(prefix="alice/work/", key_ref="research")


def test_migration_bounded_resume_and_wrong_identity_keep_intent():
    response = dict(prefix='alice/work', provider='aws-kms', keyRef='research', requestId='move-1', state='migrating', migrated=0, startedAt='2030-01-01T00:00:00.000Z', completedAt=None, ciphertext='NEVER_RETURN')
    observed = []
    def handler(request):
        observed.append(request)
        return httpx.Response(200, json=response)
    vault = Vault(httpx.Client(base_url='http://fixture', transport=httpx.MockTransport(handler)))
    result = vault.kms.migrate(prefix='alice/work', key_ref='research', request_id='move-1')
    assert 'ciphertext' not in result
    assert observed[-1].headers['idempotency-key'] == 'move-1'
    assert vault.kms.resume(request_id='move-1', limit=3) == result
    assert json.loads(observed[-1].content) == {'limit': 3}
    assert vault.kms.status(request_id='move-1') == result
    count = len(observed)
    for limit in [0, 101, True, 1.5, '1']:
        with pytest.raises(VaultError): vault.kms.resume(request_id='move-1', limit=limit)
    assert len(observed) == count
    for field, value in [('prefix', 'alice/other'), ('keyRef', 'other'), ('requestId', 'other')]:
        original = response[field]; response[field] = value
        with pytest.raises(VaultWriteError) as error: vault.kms.migrate(prefix='alice/work', key_ref='research', request_id='move-1')
        assert error.value.request_id == 'move-1'
        response[field] = original
    response['state'] = 'completed'
    with pytest.raises(VaultError): vault.kms.status(request_id='move-1')


@pytest.mark.parametrize('patch', [
    {'startedAt': '2030-02-31T00:00:00.000Z'},
    {'startedAt': '0000-01-01T00:00:00.000Z'},
    {'completedAt': '2030-02-31T00:00:00.000Z'},
    {'completedAt': '2029-12-31T00:00:00.000Z'},
])
def test_migration_rejects_impossible_or_backwards_timestamps(patch):
    value = dict(prefix='alice/work', provider='aws-kms', keyRef='research', requestId='move-1', state='completed', migrated=1, startedAt='2030-01-01T00:00:00.000Z', completedAt='2030-01-02T00:00:00.000Z')
    value.update(patch)
    vault = Vault(httpx.Client(base_url='http://fixture', transport=httpx.MockTransport(lambda _: httpx.Response(200, json=value))))
    with pytest.raises(VaultError): vault.kms.status(request_id='move-1')


def test_resume_sends_explicit_prefix_and_rejects_mismatched_response():
    value = dict(prefix='alice/other', provider='aws-kms', keyRef='research', requestId='move-1', state='migrating', migrated=1, startedAt='2030-01-01T00:00:00.000Z', completedAt=None)
    def handler(request):
        assert json.loads(request.content) == {'limit': 1, 'expectedPrefix': 'alice/work'}
        return httpx.Response(200, json=value)
    vault = Vault(httpx.Client(base_url='http://fixture', transport=httpx.MockTransport(handler)))
    with pytest.raises(VaultWriteError) as error: vault.kms.resume(request_id='move-1', limit=1, prefix='alice/work/')
    assert error.value.request_id == 'move-1'
