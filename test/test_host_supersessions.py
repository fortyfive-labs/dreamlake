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
