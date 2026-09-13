"""Real HTTP/Mongo and candidate CLI/Python; local auth adapter, not real SSH."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

import httpx
from dreamlake.host_key_rotation import _public
from dreamlake.host_key_journal import load_journal
from dreamlake.host_key_state import validate_state, validate_transition
from dreamlake.vault import Vault


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--server-dir',type=Path,required=True);parser.add_argument('--cli-dir',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='vault-rotation-http-') as temporary:
        root=Path(temporary).resolve();root.chmod(0o700);fixture=root/'fixture.json'
        server=subprocess.Popen(['node','--import','tsx','src/vault/serveHostCredentialFixture.ts',str(fixture)],cwd=args.server_dir.resolve(),start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(300):
                if fixture.exists(): break
                if server.poll() is not None: raise RuntimeError('Fixture startup failed')
                time.sleep(.1)
            else:raise RuntimeError('Fixture timeout')
            f=json.loads(fixture.read_text())
            env={**os.environ,'DREAMLAKE_REMOTE':f['url'],'DREAMLAKE_API_KEY':f['aliceToken'],'XDG_CONFIG_HOME':str(root/'config'),
                 'PATH':str(Path(__file__).parent.resolve()/'fixtures/rotation')+os.pathsep+str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH']}
            with httpx.Client(base_url=f['url'],headers={'Authorization':'Bearer '+f['aliceToken']},timeout=30) as http:
                vault=Vault(http)
                for client in ('cli','python'):
                    home=root/client;home.mkdir(mode=0o700);(home/'.ssh').mkdir(mode=0o700)
                    key=root/(client+'-key')
                    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True,capture_output=True)
                    auth=home/'.ssh/authorized_keys';auth.write_text(_public(key)+' old\n# untouched\n');auth.chmod(0o600)
                    old=vault.add('alice/'+client+'-old',key.read_text())
                    b=vault.bind_host_credential(host_id=f['hostId'],enrollment_id=f['enrollmentId'],role='target' if client=='cli' else 'jump',endpoint=client,kind='private_key',entry_id=old['id'],entry_revision=1)
                    operation=root/(client+'.json');known=root/'known_hosts';known.touch();known.chmod(0o600)
                    env['VAULT_ROTATION_FAKE_HOME']=str(home)
                    if client=='cli':
                        command=['node','--import','tsx','src/cli/index.ts','vault','rotate-key','--binding-id',b['id'],'--operation-file',str(operation),'--new-entry','alice/'+client+'-new','--ssh','fixture@fixture.invalid','--known-hosts',str(known)]
                        cwd=args.cli_dir.resolve()
                    else:
                        payload=dict(binding_id=b['id'],operation_file=str(operation),new_entry='alice/'+client+'-new',ssh=dict(host='fixture.invalid',user='fixture',port=22,knownHostsFile=str(known)))
                        request=root/'request.json';request.write_text(json.dumps(payload));request.chmod(0o600)
                        code='import os,json,sys,httpx;from dreamlake.vault import Vault;c=httpx.Client(base_url=os.environ["DREAMLAKE_REMOTE"],headers={"Authorization":"Bearer "+os.environ["DREAMLAKE_API_KEY"]});print(json.dumps(Vault(c).rotate_host_key(**json.load(open(sys.argv[1])))))'
                        command=[sys.executable,'-c',code,str(request)];cwd=root
                    result=subprocess.run(command,cwd=cwd,env=env,capture_output=True,text=True,timeout=60)
                    if result.returncode:
                        # Only source CLI static stage messages may be surfaced.
                        safe=[line for line in result.stderr.splitlines() if line.startswith('Rotation is not confirmed')]
                        raise RuntimeError(client+' rotation failed; '+''.join(safe))
                    state=load_journal(operation,validate_state,validate_transition)[0]
                    assert state['phase']=='cleanup_confirmed'
                    assert vault.host_credential_operation(state['operationId'])['state']=='cleanup_confirmed'
                    assert state['oldPublicKey'] not in auth.read_text() and '# untouched\n' in auth.read_text()
                    assert not Path(state['oldKeyPath']).exists() and not Path(state['newKeyPath']).exists()
                    print('PASS '+client+' real HTTP/Mongo lifecycle with explicitly simulated SSH adapter',flush=True)
        finally:
            if server.poll() is None:
                os.killpg(server.pid,signal.SIGTERM)
                try:server.wait(timeout=30)
                except subprocess.TimeoutExpired:os.killpg(server.pid,signal.SIGKILL);server.wait()
        assert not fixture.exists()
    print('PASS isolated fixture/database cleanup',flush=True)


if __name__=='__main__':main()
