import json
import os
from pathlib import Path
import subprocess
import httpx
import pytest

from dreamlake.vault import Vault, VaultError
from dreamlake.vault_import import discover_ssh, read_ssh_file


@pytest.fixture
def source(tmp_path):
    root = tmp_path.resolve()
    key = root / 'id'
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
    config = root / 'config'
    config.write_text(f'Host target\n HostName example.test\n User test\n ProxyJump jump\n IdentityFile {key}\nHost jump\n HostName jump.test\n IdentityFile {root / "missing"}\n')
    config.chmod(0o600)
    return config, key


def client(handler=None, url='https://vault.example'):
    def forbidden(request):
        pytest.fail('Unexpected network call')
    return Vault(httpx.Client(base_url=url, transport=httpx.MockTransport(handler or forbidden)))


def test_preview_and_empty_selection_never_read_keys(source, monkeypatch):
    config, key = source
    import dreamlake.vault_import as module
    original = module.read_ssh_file
    def read(path, private_key=False):
        assert not private_key
        return original(path)
    monkeypatch.setattr(module, 'read_ssh_file', read)
    v = client()
    preview = v.import_entries(source='ssh', prefix='alice/remote', config=config, dry_run=True)
    assert {i['id'] for i in preview['discovered']} == {'profile:target','key:target:1','profile:jump','key:jump:1'}
    assert 'PRIVATE KEY' not in json.dumps(preview)
    assert v.import_entries(source='ssh', prefix='alice', config=config, select=[])['entries'] == []
    with pytest.raises(VaultError, match='explicit selection'):
        v.import_entries(source='ssh', prefix='alice', config=config)


def test_static_parser_ignores_executables_and_dependencies():
    text = '''Include /secret/include
Match exec "touch /tmp/never"
 User ignored
Host *
 User wildcard
Host good
 HostName example.test
 ProxyCommand SECRET_NEVER_IN_REPORT
 IdentityFile %d/hardware
 IdentityFile relative
Host duplicate DUPLICATE
Host !bad good2
Host bad.escape
'''
    items, warnings = discover_ssh(text)
    assert [i['id'] for i in items] == ['profile:good']
    assert 'SECRET_NEVER_IN_REPORT' not in json.dumps([items,warnings])
    assert 'user' not in items[0]['profile']
    assert json.loads(items[0]['profile']['identityfiles']) == ['%d/hardware','relative']


@pytest.mark.parametrize('selection', [['unknown'], ['profile:target','profile:target'], 'profile:target'])
def test_bad_selection_fails_before_key_or_http(source, selection):
    with pytest.raises(VaultError):
        client().import_entries(source='ssh', prefix='alice', config=source[0], select=selection)


def test_selected_only_upload_and_jump_reference(source):
    config, key = source
    calls, saved = [], {}
    def handler(request):
        calls.append(request.method)
        if request.method == 'GET':return httpx.Response(404)
        value=json.loads(request.content);saved[value['name']]=value['value']
        return httpx.Response(200,json={'entry':{'name':value['name'],'type':'string','revision':1}})
    report=client(handler).import_entries(source='ssh',prefix='alice/remote',config=config,select=['profile:target','key:target:1'])
    assert [e['status'] for e in report['entries']]==['success','success']
    assert saved['alice/remote/ssh/keys/target-1']==key.read_text()
    assert saved['alice/remote/ssh/profiles/target']['proxyjump']=='jump'
    assert all('jump' not in name for name in saved)
    assert 'PRIVATE KEY' not in json.dumps(report)
    assert calls==['GET','PUT','GET','PUT']


@pytest.mark.parametrize('mode', ['permissions','symlink','hardlink','fifo','invalid-utf8','oversized'])
def test_unsafe_selected_files_rejected_without_write(source, mode):
    config,key=source
    if mode=='permissions':key.chmod(0o644)
    elif mode=='symlink':
        replacement=key.with_name('replacement');key.rename(replacement);key.symlink_to(replacement)
    elif mode=='hardlink':os.link(key,key.with_name('link'))
    elif mode=='fifo':key.unlink();os.mkfifo(key,0o600)
    elif mode=='invalid-utf8':key.write_bytes(b'\xff')
    elif mode=='oversized':key.write_bytes(b'x'*65537)
    result=client().import_entries(source='ssh',prefix='alice',config=config,select=['key:target:1'])
    assert result['entries'][0]['status']=='failure'


def test_config_symlink_ancestor_rejected(source):
    config,_=source
    link=config.parent.with_name(config.parent.name+'-link');link.symlink_to(config.parent,target_is_directory=True)
    with pytest.raises(VaultError,match='unsafe'):
        read_ssh_file(link/'config')


@pytest.mark.parametrize('outcome', ['committed','absent','rejected','bad-ack'])
def test_unknown_write_reconciles_without_replay(source,outcome):
    config,key=source;writes=[];reads=[];stored={}
    def handler(request):
        if request.method=='PUT':
            writes.append(1)
            data=json.loads(request.content)
            if outcome in ('committed','bad-ack'):stored.update(data)
            if outcome=='rejected':return httpx.Response(409)
            if outcome=='bad-ack':return httpx.Response(200,json={'entry':{'name':data['name'],'type':'string','revision':99}})
            raise httpx.ReadTimeout('PRIVATE_ERROR')
        if request.method=='GET':
            reads.append(1)
            return httpx.Response(200,json={'entry':{'name':stored['name'],'type':'string','revision':1}}) if stored else httpx.Response(404)
        return httpx.Response(200,json={'entries':[stored]})
    report=client(handler).import_entries(source='ssh',prefix='alice',config=config,select=['key:target:1'],retry=1)
    assert len(writes)==(2 if outcome=='rejected' else 1)
    assert report['entries'][0]['status']==('success' if outcome in ('committed','bad-ack') else 'unknown' if outcome=='absent' else 'failure')
    assert 'PRIVATE_ERROR' not in json.dumps(report) and 'PRIVATE KEY' not in json.dumps(report)


def test_conflict_requires_pinned_revision_and_equal_skips_write(source):
    config,_=source;puts=[]
    def handler(request):
        name='alice/ssh/profiles/target'
        if request.method=='GET':return httpx.Response(200,json={'entry':{'name':name,'type':'string','revision':7}})
        if request.method=='POST':return httpx.Response(200,json={'entries':[{'name':name,'type':'string','value':'different'}]})
        puts.append(request.headers.get('if-match'))
        return httpx.Response(409)
    v=client(handler)
    args=dict(source='ssh',prefix='alice',config=config,select=['profile:target'])
    assert v.import_entries(**args)['entries'][0]['status']=='failure' and not puts
    result=v.import_entries(**args,if_match={'profile:target':3},retry=1)
    assert puts==['3','3'] and result['entries'][0]['status']=='failure'


def test_source_contract_and_otp_alias(source,tmp_path):
    v=client()
    for kind in ('pass','ssh,pass-otp',None):
        with pytest.raises(VaultError):v.import_entries(source=kind,prefix='alice')
    with pytest.raises(VaultError):v.import_entries(source='ssh',prefix='alice',store=tmp_path)
    with pytest.raises(VaultError):v.import_entries(source='pass-otp',prefix='alice',select=[])
    with pytest.raises(VaultError,match='select paths'):v.import_entries(source='pass-otp',prefix='alice',store=tmp_path)
    store=tmp_path.resolve();(store/'test.gpg').write_bytes(b'ciphertext')
    decrypt=lambda _: 'ADJACENT_PASSWORD\notpauth://totp/Test?secret=JBSWY3DPEHPK3PXP'
    a=v.import_entries(source='pass-otp',prefix='alice',store=store,dry_run=True,decryptor=decrypt)
    b=v.pass_store.sync(store=store,otp=True,prefix='alice',dry_run=True,decryptor=decrypt)
    assert a==b and a['uploaded'] is False
    assert 'ADJACENT_PASSWORD' not in json.dumps(a) and 'JBSWY3DPEHPK3PXP' not in json.dumps(a)


def test_insecure_endpoint_rejected_before_source_read(source):
    with pytest.raises(VaultError,match='HTTPS'):
        client(url='http://remote.example').import_entries(source='ssh',prefix='alice',config=source[0],select=['profile:target'])


def test_hardware_key_envelope_retains_reference(source):
    import base64
    config,key=source
    lines=key.read_text().splitlines()
    body=base64.b64decode(''.join(lines[1:-1])).replace(b'ssh-ed25519',b'sk--ed25519')
    key.write_text(lines[0]+'\n'+base64.b64encode(body).decode()+'\n'+lines[-1]+'\n')
    result=client().import_entries(source='ssh',prefix='alice',config=config,select=['key:target:1'])
    assert result['entries'][0]['status']=='failure'


def test_config_changed_between_review_and_preparation(source,monkeypatch):
    import dreamlake.vault_import as module
    original=module.read_ssh_file;calls=[]
    def changing(path,private_key=False):
        assert not private_key
        calls.append(1)
        return original(path)+('# changed\n' if len(calls)>1 else '')
    monkeypatch.setattr(module,'read_ssh_file',changing)
    with pytest.raises(VaultError,match='changed since selection'):
        client().import_entries(source='ssh',prefix='alice',config=source[0],select=['key:target:1'])


@pytest.mark.parametrize('kwargs',[{'prefix':''},{'prefix':'alice','dry_run':'yes'},{'prefix':'../alice'}])
def test_canonical_validation_before_source(kwargs):
    with pytest.raises(VaultError):
        client().import_entries(source='ssh',config='/does/not/exist',**kwargs)
