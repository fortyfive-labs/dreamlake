import base64
import json
import os
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from dreamlake.vault import Vault, VaultError
from dreamlake.hotp import intent_file


def test_intent_is_private_immutable_and_never_refreshes(tmp_path):
    file = tmp_path / 'intent.json'
    binding = dict(origin='https://example.test', account='alice', name='alice/login')
    first = intent_file(file, binding, lambda: {'entry': dict(name='alice/login', type='hotp', id='stable-id', revision=1)})
    assert first['expectedRevision'] == 1
    assert intent_file(file, binding, lambda: pytest.fail('must not refresh')) == first
    assert file.stat().st_mode & 0o777 == 0o600
    assert not {'seed','counter','code','token'} & json.loads(file.read_text()).keys()
    with pytest.raises(VaultError):
        intent_file(file, dict(binding, account='bob'), lambda: pytest.fail())
    os.chmod(file,0o644)
    with pytest.raises(VaultError):
        intent_file(file,binding,lambda:pytest.fail())
    os.chmod(file,0o600)
    link=tmp_path/'link';link.symlink_to(file)
    with pytest.raises(OSError):
        intent_file(link,binding,lambda:pytest.fail())


def test_hotp_dispatch_pins_origin_and_auth_and_allows_only_result_fields(tmp_path):
    token='x.'+base64.urlsafe_b64encode(json.dumps({'sub':'alice'}).encode()).decode().rstrip('=')+'.x'
    requests=[]
    def handler(request):
        requests.append(request)
        if request.method=='GET':
            client.base_url='https://attacker.test'
            client.headers['Authorization']='Bearer OTHER_TOKEN'
            return httpx.Response(200,json={'entry':dict(name='alice/login',type='hotp',id='stable-id',revision=1)})
        body=json.loads(request.content)
        return httpx.Response(200,json=dict(type='hotp',code='123456',requestId=body['requestId'],revision=2,replayed=False,recoverUntil=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(timespec='milliseconds').replace('+00:00','Z'),seed='SENSITIVE_EXTRA'))
    with httpx.Client(base_url='https://example.test',headers={'Authorization':'Bearer '+token},transport=httpx.MockTransport(handler)) as client:
        result=json.loads(Vault(client).otp('alice/login',request_file=tmp_path/'intent.json',to_json=True))
    assert result['code']=='123456' and 'seed' not in result
    assert len(requests)==2
    assert all(str(r.url).startswith('https://example.test/') and r.headers['authorization']=='Bearer '+token for r in requests)


def test_failed_hotp_preserves_intent_and_sanitizes_exception(tmp_path):
    token='x.'+base64.urlsafe_b64encode(b'{"sub":"alice"}').decode().rstrip('=')+'.x'
    def handler(request):
        if request.method=='GET':return httpx.Response(200,json={'entry':dict(name='alice/login',type='hotp',id='stable-id',revision=1)})
        raise RuntimeError('SENSITIVE_SECRET')
    with httpx.Client(base_url='https://example.test',headers={'Authorization':'Bearer '+token},transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VaultError,match='reuse the same request_file') as error:
            Vault(client).otp('alice/login',request_file=tmp_path/'intent.json')
        assert 'SENSITIVE_SECRET' not in str(error.value)
    assert (tmp_path/'intent.json').is_file()
