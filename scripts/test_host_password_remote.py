"""Candidate native/npm/Python verification against owned private Linux SSHD.

No existing account password or system SSH/PAM policy is changed. Only fresh
synthetic users/passwords and a private loopback daemon are created. Private
fixture state must be retained if any remote cleanup is unconfirmed.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

import httpx
from dreamlake.vault import Vault
from dreamlake.host_key_journal import _directory, publish_record


def state_write(path, state):
    temporary=path.with_suffix('.pending')
    with open(temporary,'w') as stream:
        os.chmod(temporary,0o600);json.dump(state,stream);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def command(args, *, timeout=45, **kwargs):
    result=subprocess.run(args,capture_output=True,timeout=timeout,**kwargs)
    if result.returncode:raise RuntimeError('Fixture command failed; protected state retained until cleanup')
    return result


def run(args):
    work=args.work_dir.resolve();_directory(work)
    assert not any(work.iterdir())
    tag=uuid.uuid4().hex[:12];state=dict(tag=tag,setupAttempted=False,remoteRemoved=False,cleanupComplete=False,bindings=[],checks=[])
    state_path=work/'state.json';state_write(state_path,state)
    passwords={role:secrets.token_urlsafe(32) for role in ('target','jump')}
    key=work/'jump-key';command(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)])
    public=' '.join((key.with_suffix('.pub')).read_text().split()[:2])
    admin=['ssh','-F',str(args.admin_config.resolve()),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',args.admin_host]
    remote_source=(Path(__file__).parent/'fixtures/password/remote.py').read_text()
    def remote(action):
        metadata=dict(action=action,tag=tag,publicKey=public,accounts=state.get('accounts',{}))
        payload=json.dumps(metadata).encode()+b'\n'
        if action=='setup':payload+=passwords['target'].encode()+b'\n'+passwords['jump'].encode()+b'\n'
        result=command(admin+['sudo','-n','python3','-c',shlex.quote(remote_source)],input=payload,timeout=60)
        return json.loads(result.stdout)
    fixture_file=work/'fixture.json'
    backend=subprocess.Popen(['node','--import','tsx','src/vault/serveHostCredentialFixture.ts',str(fixture_file)],cwd=args.server_dir.resolve(),start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    state.update(backendPid=backend.pid,serverDir=str(args.server_dir.resolve()),adminConfig=str(args.admin_config.resolve()),adminHost=args.admin_host)
    state_write(state_path,state)
    tunnel=None;http=None;vault=None
    try:
        for _ in range(300):
            if fixture_file.exists():break
            if backend.poll() is not None:raise RuntimeError('Owned backend startup failed')
            time.sleep(.1)
        else:raise RuntimeError('Owned backend startup timeout')
        fixture=json.loads(fixture_file.read_text())
        http=httpx.Client(base_url=fixture['url'],headers={'Authorization':'Bearer '+fixture['aliceToken']},timeout=30,trust_env=False)
        vault=Vault(http)
        state['setupAttempted']=True;state_write(state_path,state)
        facts=remote('setup');state.update(remote=facts,accounts=facts['accounts']);state_write(state_path,state)
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1',0));local_port=reserve.getsockname()[1]
        tunnel=subprocess.Popen(admin[:-1]+['-o','ControlMaster=no','-o','ControlPath=none','-o','ExitOnForwardFailure=yes','-N','-L',f'127.0.0.1:{local_port}:127.0.0.1:{facts["port"]}',admin[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            if tunnel.poll() is not None:raise RuntimeError('Owned tunnel failed')
            try:
                with socket.create_connection(('127.0.0.1',local_port),timeout=.2):break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('Owned tunnel timeout')
        known=work/'known_hosts';publish_record(known,('\n'.join(f'[127.0.0.1]:{p} '+facts['hostPublicKey'] for p in {local_port,facts['port']})+'\n').encode())
        package=work/'npm';package.mkdir();launcher=package/'launcher.cjs';shutil.copyfile(args.cli_dir/'scripts/npm-launcher.cjs',launcher)
        platform=command(['node','-p','process.platform+"-"+process.arch']).stdout.decode().strip()
        binary=package/'node_modules'/'@dreamlake'/('dreamlake-cli-'+platform)/'dreamlake';binary.parent.mkdir(parents=True);binary.symlink_to(args.native.resolve())
        clients={'native':[str(args.native.resolve())],'npm':['node',str(launcher)]}
        env={**os.environ,'DREAMLAKE_REMOTE':fixture['url'],'DREAMLAKE_API_KEY':fixture['aliceToken'],'XDG_CONFIG_HOME':str(work/'config')}
        prefix='alice/password-'+tag
        bindings={}
        for role,client in [('target','native'),('jump','npm')]:
            name=prefix+'/'+role
            # Real anonymous stdin pipe crosses the native and npm launchers.
            result=command(clients[client]+['vault','add','-n',name,'--stdin','--request-id','password_'+tag+'_'+role],input=passwords[role].encode(),env=env)
            assert passwords[role].encode() not in result.stdout+result.stderr
            entry=vault.show(name)
            bound=vault.bind_host_credential(host_id=fixture['hostId'],enrollment_id=fixture['enrollmentId'],role=role,
                 endpoint=facts['accounts'][role]['user']+'@127.0.0.1',kind='password',entry_id=entry['id'],entry_revision=entry['revision'])
            bindings[role]=bound;state['bindings'].append(bound)
            wrong=vault.add(prefix+'/'+role+'-wrong',secrets.token_urlsafe(32))
            bad=vault.bind_host_credential(host_id=fixture['hostId'],enrollment_id=fixture['enrollmentId'],role=role,
                 endpoint=facts['accounts'][role]['user']+'@127.0.0.1:'+str(local_port if role=='jump' else facts['port']),kind='password',entry_id=wrong['id'],entry_revision=wrong['revision'])
            bindings[role+'-wrong']=bad;state['bindings'].append(bad);state_write(state_path,state)
            state['checks'].append(client+' stdin password saving preserved bytes and redacted output')
        for client in ('native','npm','python'):
            for role in ('jump','target'):
                profile=dict(host='127.0.0.1',user=facts['accounts'][role]['user'],port=local_port if role=='jump' else facts['port'],knownHostsFile=str(known))
                if role=='target':profile['jump']=dict(host='127.0.0.1',user=facts['accounts']['jump']['user'],port=local_port,knownHostsFile=str(known),identityFile=str(key))
                for suffix,status in [('', 'verified'),('-wrong','denied')]:
                    bound=bindings[role+suffix]
                    if client=='python':result=vault.verify_host_password(binding_id=bound['id'],ssh=profile)
                    else:
                        argv=clients[client]+['vault','verify-password','--binding-id',bound['id'],'--ssh='+f'-p {profile["port"]} {profile["user"]}@127.0.0.1','--known-hosts',str(known)]
                        if role=='target':argv+=['--jump-ssh='+f'-p {local_port} {facts["accounts"]["jump"]["user"]}@127.0.0.1','--jump-identity',str(key),'--jump-known-hosts',str(known)]
                        response=subprocess.run(argv,env=env,capture_output=True,timeout=45)
                        assert response.returncode==(0 if status=='verified' else 1)
                        assert all(value.encode() not in response.stdout+response.stderr for value in passwords.values())
                        result=json.loads(response.stdout)
                    assert result['status']==status and result['method']=='ssh_password' and result['bindingId']==bound['id']
                    if status=='verified':assert result['remoteIdentity']['uid']==facts['accounts'][role]['uid']
                message=client+' '+role+' fresh password authentication and wrong-password denial'
                state['checks'].append(message);state_write(state_path,state);print('PASS '+message,flush=True)
    finally:
        if tunnel is not None:
            if tunnel.poll() is None:tunnel.terminate()
            try:tunnel.wait(timeout=15)
            except subprocess.TimeoutExpired:tunnel.kill();tunnel.wait()
        if state['setupAttempted']:
            try:
                receipt=remote('cleanup');assert receipt['removed'];state['remoteRemoved']=True;state['remoteCleanup']=receipt;state_write(state_path,state)
            except Exception:
                state['cleanupComplete']=False;state['recoveryBackendRetained']=True;state_write(state_path,state)
                raise RuntimeError('Remote cleanup unconfirmed; private backend and state retained for recovery') from None
        if vault is not None:
            for bound in state['bindings']:
                vault.unbind_host_credential(binding_id=bound['id'],entry_id=bound['entryId'],entry_revision=bound['entryRevision'])
            for entry in vault.list(prefix='alice/password-'+tag):vault.delete(entry['name'],if_match=entry['revision'])
        if http:http.close()
        if backend.poll() is None:os.killpg(backend.pid,signal.SIGTERM)
        backend.wait(timeout=30)
        assert not fixture_file.exists()
        key.unlink(missing_ok=True);passwords.clear()
        state['cleanupComplete']=True;state_write(state_path,state)
        print('PASS private SSHD, marked users, tunnel, bindings, entries and fixture backend cleaned',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('work-dir','server-dir','cli-dir','native','admin-config'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--admin-host',required=True)
    run(parser.parse_args())
