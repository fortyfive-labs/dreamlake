import json
import httpx
import pytest
from dreamlake.vault import Vault, VaultError

INTENT = dict(binding_id='a'*8+'-'+'a'*4+'-'+'a'*4+'-'+'a'*4+'-'+'a'*12, operation_id='replace-1', expected_entry_id='old', expected_entry_revision=1, replacement_entry_id='new', replacement_entry_revision=2)
BODY = dict(operationId='replace-1', expectedEntryId='old', expectedEntryRevision=1, replacementEntryId='new', replacementEntryRevision=2)
RECEIPT = dict(**BODY, bindingId=INTENT['binding_id'], hostId='a'*24, enrollmentId='b'*24, role='jump', endpoint='jump', kind='private_key', state='cleanup_pending', createdAt='2030-01-01T00:00:00Z')

def test_explicit_metadata_contract_and_redacted_receipt():
    calls = []
    def handle(req):
        calls.append(req)
        if req.method == 'POST': assert json.loads(req.content) == BODY
        return httpx.Response(200, json={'operation': {**RECEIPT, 'unexpectedSecret': 'PRIVATE'}})
    with httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(handle)) as http:
        vault = Vault(http)
        assert vault.supersede_host_credential(**INTENT) == RECEIPT
        assert vault.host_credential_operation('replace-1') == RECEIPT
    assert calls[0].url.path.endswith('/supersede') and calls[1].method == 'GET'

@pytest.mark.parametrize('changed', [{'expected_entry_revision': True}, {'replacement_entry_id':'old'}, {'operation_id':'../escape'}, {'binding_id':'bad'}])
def test_invalid_intent_never_sends(changed):
    with httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(lambda _: pytest.fail('unexpected network'))) as http:
        with pytest.raises(VaultError): Vault(http).supersede_host_credential(**{**INTENT, **changed})

@pytest.mark.parametrize('changed', [{'operationId':'other'}, {'bindingId':'bad'}, {'replacementEntryRevision': True}, {'state':'complete'}, {'role':'other'}])
def test_mismatched_or_untrusted_receipt_rejected(changed):
    with httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'operation':{**RECEIPT, **changed}}))) as http:
        with pytest.raises(VaultError): Vault(http).supersede_host_credential(**INTENT)

PROOF = dict(oldFingerprint='SHA256:'+'A'*43, newFingerprint='SHA256:'+'B'*43,
             remoteIdentity=dict(uid=1001, home='/home/fixture', machineId='fixture', sshDirectoryDevice=1, sshDirectoryInode=2),
             newLoginVerifiedAt='2030-01-01T00:00:00Z', oldLoginDeniedAt='2030-01-01T00:01:00Z', replacementReverifiedAt='2030-01-01T00:02:00Z')


def test_cleanup_attestation_parity_replay_and_metadata_filter():
    confirmation = {k:v for k,v in BODY.items() if k != 'operationId'}
    confirmation.update(bindingId=INTENT['binding_id'], verification=PROOF)
    done = dict(RECEIPT, state='cleanup_confirmed', confirmation=confirmation,
                verificationSource='client_attestation', cleanupConfirmedAt='2030-01-01T00:03:00Z')
    def handle(req):
        if req.method == 'POST':
            assert json.loads(req.content) == confirmation
        return httpx.Response(200, json={'operation': dict(done, secret='DO_NOT_RETURN')})
    with httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(handle)) as http:
        vault = Vault(http)
        kwargs = {k:v for k,v in INTENT.items() if k != 'operation_id'}
        assert vault.confirm_host_credential_cleanup('replace-1', **kwargs, verification=PROOF) == done
        assert vault.host_credential_operation('replace-1') == done


@pytest.mark.parametrize('proof', [dict(PROOF, password='never-send'), dict(PROOF, newFingerprint=PROOF['oldFingerprint']), dict(PROOF, oldLoginDeniedAt='2029-01-01T00:00:00Z')])
def test_cleanup_invalid_proof_never_sends(proof):
    with httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(lambda _: pytest.fail('unexpected network'))) as http:
        kwargs = {k:v for k,v in INTENT.items() if k != 'operation_id'}
        with pytest.raises(VaultError):
            Vault(http).confirm_host_credential_cleanup('replace-1', **kwargs, verification=proof)


def test_lookup_metadata_and_stable_id_verification():
    binding = dict(id=INTENT['binding_id'], hostId='a'*24, enrollmentId='b'*24, role='jump', endpoint='jump', kind='private_key', entryId='new', entryRevision=2, cleanupVersion=999)
    data = {'binding': binding, 'entry': {'id':'new', 'name':'alice/new', 'type':'string', 'revision':2, 'value':'DO_NOT_RETURN'}}
    with httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as http:
        result = Vault(http).host_credential(INTENT['binding_id'])
        assert 'cleanupVersion' not in result['binding']
        assert 'value' not in result['entry']
        data['entry']['id'] = 'different'
        with pytest.raises(VaultError): Vault(http).host_credential(INTENT['binding_id'])
