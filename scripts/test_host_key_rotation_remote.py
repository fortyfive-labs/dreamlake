"""Owned real target/jump SSH rotation against an isolated real HTTP/Mongo backend.

Requires an explicitly authorized Linux admin SSH endpoint with passwordless sudo,
useradd/usermod/userdel, Python3 and OpenSSH. Creates only randomly marked temporary
accounts; does not edit system SSH policy or existing users. The protected workdir
holds fixture authentication, generated keys and recovery state until cleanup.
Uses candidate source clients; does not claim published/hosted/KMS acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import time
import uuid

import httpx
from dreamlake.vault import Vault
from dreamlake.host_key_journal import _directory, canonical_json, load_journal, publish_record
from dreamlake.host_key_state import validate_state, validate_transition
from dreamlake.host_key_rotation import _public
from dreamlake.host_key_transport import public_key_denied, ssh_config, run_helper, _AuthLog, _bounded_process


REMOTE = r'''
import json, os, pathlib, pwd, re, subprocess, sys, time
p = json.load(sys.stdin)
assert re.fullmatch(r'[0-9a-f]{12}', p['tag'])
assert p['action'] in ('setup', 'inspect', 'cleanup')
accounts = p['accounts']
assert set(accounts) == {'target', 'jump'}
def checked(role, missing=False):
    a = accounts[role]
    assert a['user'] == 'dlvr-' + role[0] + '-' + p['tag']
    assert a['marker'] == 'dreamlake-vault-rotation-' + p['tag'] + '-' + role
    try: row = pwd.getpwnam(a['user'])
    except KeyError:
        if missing: return None
        raise
    assert row.pw_gecos == a['marker'] and row.pw_uid >= 1000
    assert row.pw_dir == '/home/' + a['user']
    if 'uid' in a: assert row.pw_uid == a['uid']
    home = pathlib.Path(row.pw_dir)
    assert not home.is_symlink()
    if home.exists(): assert home.stat().st_uid == row.pw_uid
    return row
if p['action'] == 'setup':
    for role in accounts:
        try: pwd.getpwnam(accounts[role]['user'])
        except KeyError: pass
        else: raise RuntimeError('Account already exists')
    for role,a in accounts.items():
        subprocess.run(['useradd','--create-home','--shell','/bin/bash','--comment',a['marker'],a['user']],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['usermod','--password','*',a['user']],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        row=checked(role);home=pathlib.Path(row.pw_dir)
        ssh=home/'.ssh';ssh.mkdir(mode=0o700)
        auth=ssh/'authorized_keys'
        auth.write_text(a['authorized']);auth.chmod(0o600)
        os.chown(ssh,row.pw_uid,row.pw_gid);os.chown(auth,row.pw_uid,row.pw_gid)
if p['action'] == 'cleanup':
    for role in accounts:
        row=checked(role,True)
        if row is None: continue
        assert 'uid' in accounts[role]
        for sig in ('-TERM','-KILL'):
            row=checked(role)
            subprocess.run(['pkill',sig,'-u',str(row.pw_uid)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            time.sleep(.2)
        checked(role)
        subprocess.run(['userdel','--remove',row.pw_name],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for role,a in accounts.items():
        assert checked(role,True) is None
        assert not pathlib.Path('/home/'+a['user']).exists()
    print(json.dumps({'removed':True}));sys.exit(0)
result={}
for role in accounts:
    row=checked(role,True)
    if row is None: result[role]=None;continue
    auth=pathlib.Path(row.pw_dir)/'.ssh/authorized_keys'
    assert not auth.is_symlink()
    if auth.exists(): assert auth.stat().st_uid == row.pw_uid
    result[role]={'uid':row.pw_uid,'home':row.pw_dir,'authorized':auth.read_text() if auth.exists() else None,'mode':auth.stat().st_mode&0o777 if auth.exists() else None}
print(json.dumps({'accounts':result,'machineId':pathlib.Path('/etc/machine-id').read_text().strip(),
                 'hostPublicKey':pathlib.Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().strip()}))
'''


def command(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, timeout=90, **kwargs)
    if result.returncode:
        safe=[line for line in result.stderr.decode(errors='replace').splitlines() if line.startswith('Rotation is not confirmed') and len(line)<1000]
        raise RuntimeError('Fixture command failed; '+''.join(safe)+'; retain private workdir')
    return result


def save_state(path, state):
    # Mutable fixture coordination only, separate from immutable product journal.
    temporary = path.with_suffix('.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(state, stream, indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def run(args):
    work = args.work_dir.resolve()
    _directory(work, private=True)
    state_file = work/'state.json'
    if state_file.exists():
        raise ValueError('Use a fresh owned workdir; existing fixture needs explicit recovery')
    tag = uuid.uuid4().hex[:12]
    state = dict(tag=tag, accounts={}, entries=[], bindings=[], operations=[], remoteSetupAttempted=False, cleanupComplete=False)
    for role in ('target','jump'):
        key=work/(role+'-initial')
        command(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)])
        unrelated=work/(role+'-unrelated')
        command(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(unrelated)])
        unchanged='# preserve unrelated comment\n'+_public(unrelated)+' unrelated-public-key\n'
        state['accounts'][role]=dict(user='dlvr-'+role[0]+'-'+tag, marker='dreamlake-vault-rotation-'+tag+'-'+role,
            authorized='no-agent-forwarding,no-X11-forwarding '+_public(key)+' selected-original\n'+unchanged,
            unchanged=unchanged,key=str(key))
    save_state(state_file,state)
    admin=['ssh','-F',str(args.admin_config.resolve()),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',args.admin_host]
    config=command(['ssh','-G','-F',str(args.admin_config.resolve()),args.admin_host]).stdout.decode()
    resolved=dict(line.split(' ',1) for line in config.splitlines() if ' ' in line)
    hostname,port=resolved['hostname'],int(resolved['port'])
    # An unrelated synthetic forced command deliberately forges a denial line
    # after successful authentication. Neither client may accept it as revocation.
    spoof_key=work/'jump-spoof-key'
    command(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(spoof_key)])
    spoof_command="echo '"+state['accounts']['jump']['user']+'@'+hostname+": Permission denied (publickey).' >&2; exit 255"
    escaped=spoof_command.replace('\\','\\\\').replace('"','\\"')
    spoof_line='command="'+escaped+'" '+_public(spoof_key)+' synthetic-forged-denial\n'
    state['accounts']['jump']['unchanged']+=spoof_line
    state['accounts']['jump']['authorized']+=spoof_line
    save_state(state_file,state)
    def remote(action):
        body=json.dumps(dict(action=action,tag=tag,accounts=state['accounts'])).encode()
        return json.loads(command(admin+['sudo','-n','python3','-c',shlex.quote(REMOTE)],input=body).stdout)
    fixture_file=work/'fixture.json'
    server=subprocess.Popen(['node','--import','tsx','src/vault/serveHostCredentialFixture.ts',str(fixture_file)],
                            cwd=args.server_dir.resolve(),start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    state['fixtureServerPid']=server.pid
    state['serverDir']=str(args.server_dir.resolve())
    state['adminConfig']=str(args.admin_config.resolve())
    state['adminHost']=args.admin_host
    save_state(state_file,state)
    vault=None
    try:
        for _ in range(300):
            if fixture_file.exists():break
            if server.poll() is not None:raise RuntimeError('Fixture startup failed')
            time.sleep(.1)
        else:raise RuntimeError('Fixture startup timeout')
        fixture=json.loads(fixture_file.read_text())
        http=httpx.Client(base_url=fixture['url'],headers={'Authorization':'Bearer '+fixture['aliceToken']},timeout=30,trust_env=False)
        vault=Vault(http)
        # Route existence gates all remote mutation (404 is expected for unknown ID).
        probe=http.get('/v1/vault/host-credentials/'+str(uuid.uuid4()))
        assert probe.status_code==404 and probe.json()['error']['code']=='BINDING_NOT_FOUND'
        state['remoteSetupAttempted']=True;save_state(state_file,state)
        facts=remote('setup')
        for role in ('target','jump'):
            state['accounts'][role]['uid']=facts['accounts'][role]['uid']
            assert facts['accounts'][role]['authorized']==state['accounts'][role]['authorized']
        save_state(state_file,state)
        known=work/'known_hosts'
        known.write_text(f'[{hostname}]:{port} '+facts['hostPublicKey']+'\n'+hostname+' '+facts['hostPublicKey']+'\n');known.chmod(0o600)
        probe_key=work/'diagnostic-key'
        command(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(probe_key)])
        for role in ('jump','target'):
            profile=dict(host=hostname,user=state['accounts'][role]['user'],port=port,knownHostsFile=str(known))
            if role=='target': profile['jump']=dict(host=hostname,user=state['accounts']['jump']['user'],port=port,
                    knownHostsFile=str(known),identityFile=state['accounts']['jump']['key'])
            cfg=work/(role+'-initial-probe-config')
            publish_record(cfg,ssh_config(profile,state['accounts'][role]['key']).encode())
            probe=subprocess.run(['ssh','-F',str(cfg),'rotation-target','printf fixture-ready'],capture_output=True,timeout=30,env={**os.environ,'LC_ALL':'C'})
            if probe.returncode:
                reason='selected-publickey-denied' if public_key_denied(probe.returncode,probe.stderr.decode(),profile) else 'transport-unconfirmed'
                raise RuntimeError('Initial '+role+' SSH '+reason)
            assert probe.stdout==b'fixture-ready'
            print('PASS initial '+role+' fresh selected-key SSH',flush=True)
            reply=run_helper(profile=profile,selected_key=state['accounts'][role]['key'],config_file=cfg,
                request=dict(action='inspect',operationId=str(uuid.uuid4()),oldPublicKey=_public(Path(state['accounts'][role]['key'])),newPublicKey=_public(probe_key)))
            assert reply['oldPresent'] and not reply['newPresent']
            print('PASS initial '+role+' real standalone helper inspect',flush=True)
        spoof_profile=dict(host=hostname,user=state['accounts']['jump']['user'],port=port,knownHostsFile=str(known))
        spoof_config=work/'spoof-baseline-config'
        publish_record(spoof_config,ssh_config(spoof_profile,str(spoof_key)).encode())
        auth_log=_AuthLog(work)
        try:
            code,_,stderr=_bounded_process(['ssh','-F',str(spoof_config),'-v','-E',str(auth_log.path),'rotation-target','true'],b'',monitor=auth_log.check_size)
            evidence=auth_log.read()
            assert code==255 and 'Authenticated to ' in evidence
            assert (spoof_profile['user']+'@'+hostname+': Permission denied (publickey).') in stderr.decode().splitlines()
            assert not public_key_denied(code,evidence,spoof_profile)
        finally:auth_log.close()
        spoof_request=dict(action='inspect',operationId=str(uuid.uuid4()),oldPublicKey=_public(spoof_key),newPublicKey=_public(probe_key))
        try:
            run_helper(profile=spoof_profile,selected_key=str(spoof_key),config_file=work/'spoof-python-config',request=spoof_request,expect_denied=True)
        except ValueError:pass
        else:raise RuntimeError('Python accepted authenticated forced-command output as denial')
        options=work/'spoof-cli-options.json'
        publish_record(options,canonical_json(dict(profile=spoof_profile,selectedKey=str(spoof_key),configFile=str(work/'spoof-cli-config'),request=spoof_request,expectDenied=True)))
        code="import fs from 'node:fs';import {runRotationHelper} from './src/cli/vault/rotationRemote.ts';const o=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));let rejected=false;try{await runRotationHelper(o)}catch{rejected=true}if(!rejected)process.exit(1);console.log('guard-rejected')"
        assert command(['node','--import','tsx','--input-type=module','-e',code,str(options)],cwd=args.cli_dir.resolve()).stdout.strip()==b'guard-rejected'
        print('PASS both clients reject real authenticated forced-command denial forgery',flush=True)
        for role in ('target','jump'):
            name=f'alice/rotation-{tag}/{role}-initial'
            entry=vault.add(name,Path(state['accounts'][role]['key']).read_text())
            state['entries'].append(dict(name=name,id=entry['id']))
            bound=vault.bind_host_credential(host_id=fixture['hostId'],enrollment_id=fixture['enrollmentId'],role=role,
                    endpoint=state['accounts'][role]['user']+'@'+hostname,kind='private_key',entry_id=entry['id'],entry_revision=entry['revision'])
            state['bindings'].append(bound);save_state(state_file,state)
        for role,client in [('target','cli'),('jump','python'),('target','python'),('jump','cli')]:
            bound=next(b for b in state['bindings'] if b['role']==role)
            name=f'alice/rotation-{tag}/{role}-{client}'
            operation_file=work/(role+'-'+client+'.json')
            profile=dict(host=hostname,user=state['accounts'][role]['user'],port=port,knownHostsFile=str(known))
            if role=='target':profile['jump']=dict(host=hostname,user=state['accounts']['jump']['user'],port=port,
                    knownHostsFile=str(known),identityFile=state['accounts']['jump']['key'])
            env={**os.environ,'DREAMLAKE_REMOTE':fixture['url'],'DREAMLAKE_API_KEY':fixture['aliceToken'],'XDG_CONFIG_HOME':str(work/'config')}
            if client=='cli':
                argv=['node','--import','tsx','src/cli/index.ts','vault','rotate-key','--binding-id',bound['id'],
                      '--operation-file',str(operation_file),'--new-entry',name,'--ssh='+f'-p {port} {profile["user"]}@{hostname}',
                      '--known-hosts',str(known)]
                if role=='target':argv+=['--jump-ssh='+f'-p {port} {profile["jump"]["user"]}@{hostname}',
                                        '--jump-identity',profile['jump']['identityFile'],'--jump-known-hosts',str(known)]
                result=command(argv,cwd=args.cli_dir.resolve(),env=env)
                assert json.loads(result.stdout)['phase']=='cleanup_confirmed'
            else:
                result=vault.rotate_host_key(binding_id=bound['id'],operation_file=operation_file,new_entry=name,ssh=profile)
                assert result['phase']=='cleanup_confirmed'
            operation=load_journal(operation_file,validate_state,validate_transition)[0]
            assert operation['phase']=='cleanup_confirmed'
            assert not Path(operation['oldKeyPath']).exists() and not Path(operation['newKeyPath']).exists()
            receipt=vault.host_credential_operation(operation['operationId'])
            assert receipt['state']=='cleanup_confirmed' and receipt['verificationSource']=='client_attestation'
            state['operations'].append(dict(role=role,client=client,operationId=operation['operationId'],phase=operation['phase']))
            entry=vault.show(name);state['entries'].append(dict(name=name,id=entry['id']))
            newkey=work/(role+'-'+client+'-restored')
            publish_record(newkey,vault.get(name).encode())
            # Independent old-key denial before updating the current fixture key.
            oldkey=state['accounts'][role]['key']
            denial_config=work/(role+'-'+client+'-denied-config')
            publish_record(denial_config,ssh_config(profile,oldkey).encode())
            auth_log=_AuthLog(work)
            try:
                code,_,_=_bounded_process(['ssh','-F',str(denial_config),'-v','-E',str(auth_log.path),'rotation-target','true'],b'',monitor=auth_log.check_size)
                assert public_key_denied(code,auth_log.read(),profile)
            finally:auth_log.close()
            fresh_config=work/(role+'-'+client+'-fresh-config')
            publish_record(fresh_config,ssh_config(profile,str(newkey)).encode())
            assert command(['ssh','-F',str(fresh_config),'rotation-target','printf rotation-ok']).stdout==b'rotation-ok'
            facts=remote('inspect')
            expected='no-agent-forwarding,no-X11-forwarding '+operation['newPublicKey']+' dreamlake-vault:'+operation['operationId']+'\n'+state['accounts'][role]['unchanged']
            assert facts['accounts'][role]['authorized']==expected
            assert facts['accounts'][role]['mode']==0o600
            state['accounts'][role]['key']=str(newkey)
            save_state(state_file,state)
            print('PASS '+client+' '+role+' real SSH rotation; restored new access, old denial, exact unrelated bytes/mode',flush=True)
    finally:
        cleanup_errors=[]
        if state['remoteSetupAttempted']:
            try:
                facts=remote('inspect')
                for role in ('target','jump'):
                    if facts['accounts'][role]:state['accounts'][role]['uid']=facts['accounts'][role]['uid']
                save_state(state_file,state)
                assert remote('cleanup')['removed']
                state['remoteRemoved']=True
            except Exception:cleanup_errors.append('remote accounts')
        if state['remoteSetupAttempted'] and not state.get('remoteRemoved'):
            state['cleanupComplete']=False
            state['cleanupErrors']=cleanup_errors
            state['recoveryBackendRetained']=True
            save_state(state_file,state)
            if vault: http.close()
            raise RuntimeError('Remote cleanup unconfirmed; backend, fixture auth, keys and state retained for recovery')
        if vault:
            try:
                # Real account removal makes fixture access unusable. Current
                # bindings are then explicitly released; old pending references
                # remain only inside the owned disposable backend if test failed.
                for bound in state['bindings']:
                    current=vault.host_credential(bound['id'])['binding']
                    if current.get('releasedAt') is None:
                        vault.unbind_host_credential(binding_id=current['id'],entry_id=current['entryId'],entry_revision=current['entryRevision'])
                # Discover any committed replacement whose response was lost.
                entries=vault.list(prefix=f'alice/rotation-{tag}')
                for entry in entries:
                    meta=vault.show(entry['name'])
                    if not meta.get('deleteAt'):vault.delete(meta['name'],if_match=meta['revision'])
                http.close()
            except Exception:cleanup_errors.append('vault records')
        if server.poll() is None:
            os.killpg(server.pid,signal.SIGTERM)
            try:server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid,signal.SIGKILL);server.wait();cleanup_errors.append('backend normal shutdown')
        if fixture_file.exists():cleanup_errors.append('fixture removal')
        state['cleanupComplete']=not cleanup_errors
        state['cleanupErrors']=cleanup_errors
        save_state(state_file,state)
        if cleanup_errors:raise RuntimeError('Cleanup incomplete: '+', '.join(cleanup_errors)+'; retain private workdir')
        # Delete only known synthetic key files after all remote/API cleanup.
        for path in work.iterdir():
            if path.is_file() and path.name not in ('state.json',) and (path.name.endswith('-initial') or path.name.endswith('-restored') or path.name.endswith('-unrelated') or path.name in ('diagnostic-key','jump-spoof-key')):
                path.unlink()
        for folder in work.glob('.rotation-*'):
            for key in ('old-key','new-key'):
                (folder/key).unlink(missing_ok=True)
        print('PASS owned remote accounts removed, current bindings released, entries retired, fixture database removed',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--server-dir',type=Path,required=True)
    parser.add_argument('--cli-dir',type=Path,required=True)
    parser.add_argument('--admin-config',type=Path,required=True)
    parser.add_argument('--admin-host',required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    run(parser.parse_args())
