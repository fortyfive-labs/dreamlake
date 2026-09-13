#!/usr/bin/env python3
"""Candidate CLI/Python reservation parity over isolated native Mongo + real HTTP.

Starts only a loopback synthetic backend. Never connects to SSH or cloud APIs.
All password authentication attestations are deliberately synthetic metadata.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import httpx
from dreamlake.vault import Vault, VaultError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-dir',type=Path,required=True)
    parser.add_argument('--cli-dir',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='vault-password-contract-') as directory:
        work=Path(directory);work.chmod(0o700);fixture=work/'fixture.json'
        with (work/'backend.log').open('wb') as log:
            backend=subprocess.Popen(['node','--import','tsx','src/vault/serveHostCredentialFixture.ts',str(fixture)],cwd=args.server_dir,stdout=log,stderr=log,env={**os.environ,'NODE_ENV':'test'})
            try:
                for _ in range(300):
                    if fixture.exists():break
                    if backend.poll() is not None:raise RuntimeError('Synthetic backend startup failed')
                    time.sleep(.1)
                data=json.loads(fixture.read_text());url=urlsplit(data['url'])
                assert url.scheme=='http' and url.hostname=='127.0.0.1'
                env={**os.environ,'DREAMLAKE_REMOTE':data['url'],'DREAMLAKE_API_KEY':data['aliceToken']}
                def cli(*arguments,secret=False,who='alice',success=True):
                    result=subprocess.run(['node','--import','tsx','src/cli/index.ts','vault','password-rotation',*arguments],cwd=args.cli_dir,env={**env,'DREAMLAKE_API_KEY':data[who+'Token']},capture_output=True,text=True,timeout=30)
                    if success and result.returncode:raise RuntimeError('CLI password operation failed')
                    if not success:
                        assert result.returncode!=0 and 'SYNTHETIC_PASSWORD' not in result.stdout+result.stderr
                        return None
                    assert 'SYNTHETIC_PASSWORD' not in result.stderr
                    if not secret:assert 'SYNTHETIC_PASSWORD' not in result.stdout
                    return result.stdout if secret else json.loads(result.stdout)
                def private(name,value):
                    path=work/name;path.write_text(json.dumps(value));path.chmod(0o600);return str(path)
                def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
                checks=[]
                with httpx.Client(base_url=data['url'],headers={'Authorization':'Bearer '+data['aliceToken']},timeout=30) as http:
                    vault=Vault(http)
                    for index,role in enumerate(('target','jump')):
                        operation_id='contract-'+role
                        old=vault.add(data['prefix']+'/'+role+'-old','SYNTHETIC_PASSWORD_OLD')
                        new=vault.add(data['prefix']+'/'+role+'-new','SYNTHETIC_PASSWORD_NEW')
                        binding=vault.bind_host_credential(host_id=data['hostId'],enrollment_id=data['enrollmentId'],role=role,endpoint='fixture@'+role,kind='password',entry_id=old['id'],entry_revision=old['revision'])
                        identity=dict(user='fixture',uid=1000,home='/home/fixture',machineId='a'*32,homeDevice=1,homeInode=2)
                        intent=dict(expectedEntryId=old['id'],expectedEntryRevision=old['revision'],replacementEntryId=new['id'],replacementEntryRevision=new['revision'],remoteIdentity=identity,recoveryKeyFingerprint='SHA256:'+'a'*43,recoveryVerifiedAt=now())
                        path=private(role+'-intent.json',intent)
                        reserve_args=('reserve','--operation-id',operation_id,'--binding-id',binding['id'],'--input',path)
                        if index==0:cli(*reserve_args)  # Treat response as lost; recover cross-client.
                        else:vault.reserve_host_password_rotation(binding_id=binding['id'],operation_id=operation_id,intent=intent)
                        assert vault.host_password_rotation(operation_id)['state']=='reserved'
                        assert cli(*reserve_args)['state']=='reserved'
                        assert cli('read','--operation-id',operation_id,'--slot','old',secret=True)=='SYNTHETIC_PASSWORD_OLD'
                        assert vault.read_host_password_rotation(operation_id,slot='new')=='SYNTHETIC_PASSWORD_NEW'
                        cli('show','--operation-id',operation_id,who='bob',success=False)
                        vault.add(old['name'],'shared source changed',if_match=1)
                        assert vault.read_host_password_rotation(operation_id,slot='old')=='SYNTHETIC_PASSWORD_OLD'
                        try:vault.add(new['name'],'forbidden overwrite',if_match=1);raise AssertionError('Frozen replacement was writable')
                        except VaultError as error:assert getattr(error,'status',None)==409
                        if index==0:vault.start_host_password_rotation(operation_id)
                        else:cli('start','--operation-id',operation_id)
                        cli('cancel','--operation-id',operation_id,success=False)
                        observed=now()
                        proof=dict(method='ssh_password',remoteIdentity=identity,recoveryKeyFingerprint=intent['recoveryKeyFingerprint'],newLoginVerifiedAt=observed,oldLoginDeniedAt=observed,replacementReverifiedAt=observed,recoveryReverifiedAt=observed)
                        proof_path=private(role+'-proof.json',proof)
                        if index==0:cli('confirm','--operation-id',operation_id,'--input',proof_path)
                        else:vault.confirm_host_password_rotation(operation_id,proof=proof)
                        assert vault.confirm_host_password_rotation(operation_id,proof=proof)['state']=='confirmed'
                        assert cli('show','--operation-id',operation_id)['state']=='confirmed'
                        cli('read','--operation-id',operation_id,'--slot','old',success=False)
                        vault.unbind_host_credential(binding_id=binding['id'],entry_id=new['id'],entry_revision=1)
                        checks.append(role+' cross-client reserve/replay/read/start/confirm, owner denial and freeze')
                print(json.dumps({'status':'passed','checks':checks,'scope':'candidate clients; real loopback HTTP/native Mongo; synthetic attestations, no SSH or cloud mutation'}))
            finally:
                if backend.poll() is None:backend.terminate()
                try:backend.wait(timeout=30)
                except subprocess.TimeoutExpired:backend.kill();backend.wait(timeout=5);raise RuntimeError('Synthetic backend forced stop; inspect cleanup')
                assert not fixture.exists(),'Backend fixture cleanup unconfirmed'

if __name__=='__main__':main()
