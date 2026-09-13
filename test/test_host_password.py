import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from dreamlake.host_password import password_bytes, password_config, probe_password
from dreamlake.vault import VaultError

PROFILE = dict(host='127.0.0.1', user='fixture', port=2222, knownHostsFile='/tmp/trusted-hosts')
FAKE = r'''import os,sys,subprocess,json,re
mode=os.environ['PROBE_CASE'];args=sys.argv[1:];log=args[args.index('-E')+1]
if os.environ.get('PROBE_DIR_REPORT'):open(os.environ['PROBE_DIR_REPORT'],'w').write(os.path.dirname(log))
if mode=='network':sys.exit(255)
assert all('test-password' not in v for v in os.environ.values())
prompt="fixture@127.0.0.1's password: "
if mode=='prompt':prompt='OTP:'
r=subprocess.run([os.environ['SSH_ASKPASS'],prompt],capture_output=True)
if mode=='prompt':assert r.returncode!=0
else:assert r.returncode==0 and r.stdout==b'test-password\n'
if mode=='twice':
 r=subprocess.run([os.environ['SSH_ASKPASS'],prompt],capture_output=True);assert r.returncode!=0
lines=['debug1: Next authentication method: password']
if mode=='denied':
 lines+=['debug1: No more authentication methods to try.','fixture@127.0.0.1: Permission denied (publickey,password).']
else:lines+=['Authenticated to 127.0.0.1 ([127.0.0.1]:2222) using "'+('publickey' if mode=='alternate' else 'password')+'".']
open(log,'w').write('\n'.join(lines) if mode!='overflow' else 'x'*65537)
if mode=='forged':
 print('fixture@127.0.0.1: Permission denied (password).',file=sys.stderr);sys.exit(255)
if mode=='denied':sys.exit(255)
nonce=re.search(r'[a-f0-9]{8}-[a-f0-9-]{27}',args[-1]).group()
print(json.dumps({'nonce':nonce if mode!='nonce' else 'wrong','identity':{'uid':1001,'user':'fixture','home':'/home/fixture','machineId':'fixture-machine'}}))
'''


@pytest.mark.parametrize('case', ['verified', 'denied', 'network', 'prompt', 'twice', 'alternate', 'forged', 'overflow', 'nonce'])
def test_password_transport_uses_real_private_askpass_and_fails_closed(tmp_path, monkeypatch, case):
    executable = tmp_path / 'fake-ssh'
    executable.write_text('#!' + sys.executable + '\n' + FAKE)
    executable.chmod(0o700)
    monkeypatch.setenv('PROBE_CASE', case)
    report=tmp_path/'owned-directory'
    monkeypatch.setenv('PROBE_DIR_REPORT',str(report))
    if case in ('verified', 'denied'):
        result = probe_password(profile=PROFILE, password='test-password', executable=str(executable))
        assert result['status'] == case
        assert 'test-password' not in json.dumps(result)
    else:
        with pytest.raises(VaultError, match='Password authentication unconfirmed'):
            probe_password(profile=PROFILE, password='test-password', executable=str(executable))
    assert not Path(report.read_text()).exists()


@pytest.mark.parametrize('value', ['', 'x\n', 'x\r', 'x\0', 'x'*513, '\ud800'])
def test_password_input_rejects_unsupported_bytes(value):
    with pytest.raises((ValueError, UnicodeError)):
        password_bytes(value)


def test_password_only_config_resolves_without_alternate_identity(tmp_path):
    profile = {**PROFILE, 'jump': {**PROFILE, 'user': 'jump', 'identityFile': '/tmp/selected-jump'}}
    config = tmp_path / 'config'
    config.write_text(password_config(profile))
    result = subprocess.run(['ssh', '-G', '-F', str(config), 'rotation-target'], capture_output=True, text=True, check=True)
    fields = dict(line.split(' ', 1) for line in result.stdout.splitlines())
    assert fields['preferredauthentications'] == 'password'
    assert fields['pubkeyauthentication'] == 'false'
    assert fields['passwordauthentication'] == 'yes'
    assert fields['kbdinteractiveauthentication'] == 'no'
    assert fields['numberofpasswordprompts'] == '1'
    jump = subprocess.run(['ssh', '-G', '-F', str(config), 'rotation-jump'], capture_output=True, text=True, check=True)
    assert 'preferredauthentications publickey\n' in jump.stdout
    assert 'passwordauthentication no\n' in jump.stdout

@pytest.mark.parametrize('mode', ['verified', 'wrong-read-id', 'stale-after-read', 'stale-after-probe', 'released', 'other-kind', 'endpoint-mismatch'])
def test_sdk_pins_auth_and_exact_binding_entry_across_probe(monkeypatch, mode):
    import base64
    import httpx
    import dreamlake.host_password as module
    from dreamlake.vault import Vault
    binding_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    binding = dict(id=binding_id, hostId='a'*24, enrollmentId='b'*24, role='target', endpoint='fixture@127.0.0.1', kind='password', entryId='entry', entryRevision=1)
    entry = dict(id='entry', name='alice/fixture', type='string', revision=1)
    calls = []
    token = 'x.' + base64.urlsafe_b64encode(b'{"sub":"alice"}').decode().rstrip('=') + '.x'
    def handle(request):
        assert request.url.host == 'fixture.test' and request.headers['Authorization'] == 'Bearer ' + token
        calls.append(request.method)
        if request.method == 'POST':
            http.base_url='https://changed.test'
            http.headers['Authorization']='Bearer changed'
            return httpx.Response(200, json={'entries': [{**entry, 'id': 'other' if mode=='wrong-read-id' else 'entry', 'value': 'test-password'}]})
        current = dict(binding)
        if mode=='released':current['releasedAt']='2030-01-01T00:00:00Z'
        if mode=='other-kind':current['kind']='private_key'
        if mode=='endpoint-mismatch':current['endpoint']='other@elsewhere'
        if mode=='stale-after-read' and len(calls)>=3 or mode=='stale-after-probe' and len(calls)>=4:current['entryRevision']=2
        return httpx.Response(200, json={'binding': current, 'entry': entry})
    probes = []
    def probe(**kwargs):
        assert kwargs['password']=='test-password'
        probes.append(True)
        return {'status':'verified','method':'ssh_password','observedAt':'2030-01-01T00:00:00Z'}
    monkeypatch.setattr(module,'probe_password',probe)
    with httpx.Client(base_url='https://fixture.test',headers={'Authorization':'Bearer '+token},transport=httpx.MockTransport(handle)) as http:
        if mode=='verified':
            result=Vault(http).verify_host_password(binding_id=binding_id,ssh=PROFILE)
            assert result['entryId']=='entry' and result['status']=='verified'
        else:
            with pytest.raises(VaultError,match='Password authentication unconfirmed'):
                Vault(http).verify_host_password(binding_id=binding_id,ssh=PROFILE)
    assert len(probes)==(1 if mode in ('verified','stale-after-probe') else 0)

@pytest.mark.parametrize('endpoint,accepted',[
    ('fixture@127.0.0.1',True),('fixture@127.0.0.1:2222',True),
    ('fixture@127.0.0.1:22',False),('other@127.0.0.1',False),
    ('fixture@elsewhere',False),('alias-only',False),('fixture:secret@127.0.0.1',False),
])
def test_binding_endpoint_must_match_explicit_target(endpoint,accepted):
    from dreamlake.host_password import match_password_endpoint
    if accepted:match_password_endpoint(endpoint,PROFILE)
    else:
        with pytest.raises(ValueError):match_password_endpoint(endpoint,PROFILE)


def test_invalid_jump_source_still_zeroes_password_buffer(monkeypatch):
    import dreamlake.host_password as module
    secret=bytearray(b'synthetic')
    monkeypatch.setattr(module,'password_bytes',lambda _:secret)
    def fail(_):raise ValueError('invalid source')
    monkeypatch.setattr(module,'read_record',fail)
    with pytest.raises(VaultError):
        module.probe_password(profile={**PROFILE,'jump':{**PROFILE,'identityFile':'/tmp/absent'}},password='unused')
    assert secret==bytearray(len(secret))
