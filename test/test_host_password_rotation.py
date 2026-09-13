import json
from copy import deepcopy
import httpx
import pytest
from dreamlake.vault import Vault, VaultError
from dreamlake.host_password_rotation import validate_intent, validate_proof, receipt

IDENTITY = dict(user='fixture', uid=1000, home='/home/fixture', machineId='a'*32, homeDevice=1, homeInode=2)
INTENT = dict(expectedEntryId='old',expectedEntryRevision=1,replacementEntryId='new',replacementEntryRevision=1,remoteIdentity=IDENTITY,recoveryKeyFingerprint='SHA256:'+'a'*43,recoveryVerifiedAt='2026-09-13T00:00:00.000Z')
OP = dict(**INTENT,operationId='rotation',bindingId='1'*36,hostId='a'*24,enrollmentId='b'*24,role='target',endpoint='fixture@host',state='reserved',createdAt='2026-09-13T00:00:01.000Z',verificationSource='client_attestation')
PROOF = dict(method='ssh_password',remoteIdentity=IDENTITY,recoveryKeyFingerprint=INTENT['recoveryKeyFingerprint'],newLoginVerifiedAt='2026-09-13T00:00:03.000Z',oldLoginDeniedAt='2026-09-13T00:00:04.000Z',replacementReverifiedAt='2026-09-13T00:00:05.000Z',recoveryReverifiedAt='2026-09-13T00:00:06.000Z')

@pytest.mark.parametrize('change',[{'password':'DO_NOT_SEND'}, {'expectedEntryRevision':True}, {'replacementEntryId':'old'}, {'recoveryVerifiedAt':'2026-02-31T00:00:00.000Z'}, {'remoteIdentity':{**IDENTITY,'password':'DO_NOT_SEND'}}])
def test_invalid_metadata_never_contacts_server(change):
    calls=[]
    with httpx.Client(base_url='https://fixture.test',transport=httpx.MockTransport(lambda r:calls.append(r))) as http:
        with pytest.raises(VaultError):Vault(http).reserve_host_password_rotation(binding_id=OP['bindingId'],operation_id='rotation',intent={**INTENT,**change})
    assert calls==[]

@pytest.mark.parametrize('change',[{'value':'DO_NOT_ECHO'}, {'verification':{'password':'DO_NOT_ECHO'}}, {'operationId':'other'}, {'state':'confirmed'}, {'startedAt':'2026-09-13T00:00:02.000Z'}])
def test_metadata_receipt_rejects_secret_or_wrong_scope(change):
    with pytest.raises(VaultError) as error:receipt({'operation':{**OP,**change}},'rotation')
    assert 'DO_NOT_ECHO' not in str(error.value)


def test_sdk_roundtrip_and_exact_explicit_snapshot_read():
    state=deepcopy(OP); calls=[]
    def handle(request):
        assert request.headers['authorization']=='Bearer synthetic'
        assert request.url.host=='fixture.test'
        calls.append(request.url.path)
        body=json.loads(request.content) if request.content else None
        if request.url.path.endswith('/read'):
            return httpx.Response(200,json=dict(operationId='rotation',slot=body['slot'],entryId='old',entryRevision=1,value='SYNTHETIC_PASSWORD'))
        if request.method=='PUT':assert body==INTENT
        if request.url.path.endswith('/start'):state.update(state='mutation_pending',startedAt='2026-09-13T00:00:02.000Z')
        if request.url.path.endswith('/confirm'):
            assert body==PROOF
            state.update(state='confirmed',verification=PROOF,terminalAt='2026-09-13T00:00:07.000Z',purgeAt='2026-10-13T00:00:07.000Z')
        return httpx.Response(200,json={'operation':state})
    with httpx.Client(base_url='https://fixture.test',headers={'authorization':'Bearer synthetic'},transport=httpx.MockTransport(handle)) as http:
        vault=Vault(http)
        assert vault.reserve_host_password_rotation(binding_id=OP['bindingId'],operation_id='rotation',intent=INTENT)['state']=='reserved'
        assert vault.read_host_password_rotation('rotation',slot='old')=='SYNTHETIC_PASSWORD'
        assert vault.start_host_password_rotation('rotation')['state']=='mutation_pending'
        assert vault.confirm_host_password_rotation('rotation',proof=PROOF)['state']=='confirmed'
        with pytest.raises(VaultError):vault.read_host_password_rotation('rotation',slot='old')
    assert sum(path.endswith('/read') for path in calls)==1


def test_wrong_snapshot_revision_is_never_returned():
    calls=[]
    def handle(request):
        calls.append(request.method)
        return httpx.Response(200,json={'operation':OP} if request.method=='GET' else dict(operationId='rotation',slot='old',entryId='old',entryRevision=2,value='DO_NOT_RETURN'))
    with httpx.Client(base_url='https://fixture.test',headers={'Authorization':'Bearer synthetic'},transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(VaultError) as error:Vault(http).read_host_password_rotation('rotation',slot='old')
        assert 'DO_NOT_RETURN' not in str(error.value)

    assert calls==['GET','POST']


def test_proof_is_password_typed_ordered_and_copied():
    value=validate_proof(PROOF);value['remoteIdentity']['uid']=2000;assert IDENTITY['uid']==1000
    with pytest.raises(VaultError):validate_proof({**PROOF,'method':'ssh_key'})
    with pytest.raises(VaultError):validate_proof({**PROOF,'oldLoginDeniedAt':'2026-09-13T00:00:01.000Z'})
    value=validate_intent(INTENT);value['remoteIdentity']['uid']=2000;assert IDENTITY['uid']==1000


def test_snapshot_read_pins_origin_and_account_across_requests():
    seen=[]
    def handle(request):
        seen.append((request.url.host,request.headers['authorization']))
        if request.method=='GET':
            http.base_url='https://changed.test'
            http.headers['Authorization']='Bearer changed'
            return httpx.Response(200,json={'operation':OP})
        return httpx.Response(200,json=dict(operationId='rotation',slot='old',entryId='old',entryRevision=1,value='SYNTHETIC_PASSWORD'))
    with httpx.Client(base_url='https://fixture.test',headers={'Authorization':'Bearer original'},transport=httpx.MockTransport(handle)) as http:
        assert Vault(http).read_host_password_rotation('rotation',slot='old')=='SYNTHETIC_PASSWORD'
    assert seen==[('fixture.test','Bearer original')]*2
