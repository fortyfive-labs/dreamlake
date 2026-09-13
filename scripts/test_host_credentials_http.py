"""CLI/Python post-enrollment saving against an isolated real HTTP/Mongo fixture.
Usage: uv run python scripts/test_host_credentials_http.py /secure/fixture.json --cli /path/to/cli
No actual SSH enrollment is claimed. The fixture owns binding/database cleanup.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
from urllib.parse import urlsplit
import httpx
from dreamlake.host_credentials import HostCredential, save_enrollment_credentials
from dreamlake.vault import Vault


def run(fixture, cli):
    if fixture.is_symlink() or not fixture.is_file() or fixture.stat().st_mode & 0o077:
        raise ValueError('Protected fixture required')
    config = json.loads(fixture.read_text())
    if urlsplit(config['url']).hostname not in ('127.0.0.1','localhost'):
        raise ValueError('Isolated loopback fixture required')
    run_id = uuid.uuid4().hex
    with httpx.Client(base_url=config['url'], headers={'Authorization':'Bearer '+config['aliceToken']}, timeout=30) as http:
        vault = Vault(http)
        result = {'enrolled':True, 'host':{'id':config['hostId']}, 'enrollment':{'id':config['enrollmentId']}}
        selections = [HostCredential(role, 'http-'+role+'-'+run_id, config['prefix']+'/'+role+'-'+run_id, 'password', password=role+'-synthetic\n') for role in ('target','jump')]
        report = save_enrollment_credentials(vault, result, selections, run_id)
        try:
            assert report['status']=='saved'
            for c,item in zip(selections,report['entries']):
                assert vault.get(c.entry_name) == c.password
                assert vault.bind_host_credential(**item['binding'])['id'] == item['bindingId']
                assert vault.write_status(request_id=item['requestId'])['entry']['id']==item['entryId']
            if cli:
                with tempfile.TemporaryDirectory(prefix='vault-host-http-') as directory:
                    root=Path(directory); password=root/'password'; password.write_bytes(b'cli-synthetic\n'); password.chmod(0o600)
                    source='''import{readFileSync,openSync,closeSync}from'node:fs';
import{saveCredentials}from'./src/cli/hosts/credentials.ts';
const [file,key,run]=process.argv.slice(1),c=JSON.parse(readFileSync(file,'utf8')),fd=openSync(key,'r');
try{const r=await saveCredentials({enrolled:true,host:{id:c.hostId},enrollment:{id:c.enrollmentId}},{ssh:{args:['cli-'+run]}},{saveCredentials:true,json:true,requestId:run,targetPassword:[c.prefix+'/cli-'+run],passwordFd:[c.prefix+'/cli-'+run+'='+fd]},{remote:c.url,token:c.aliceToken}); console.log(JSON.stringify(r));}finally{closeSync(fd)}'''
                    process=subprocess.run(['node','--import','tsx','--input-type=module','-e',source,str(fixture.absolute()),str(password),run_id],cwd=cli,capture_output=True,text=True,timeout=60,check=True)
                    cli_report=json.loads(process.stdout)
                    report['entries'].extend(cli_report['entries'])
                    assert cli_report['status']=='saved'
                    assert vault.get(config['prefix']+'/cli-'+run_id)=='cli-synthetic\n'
            print('PASS: real HTTP/Mongo Python and CLI save/bind, target+jump separation, exact bytes, metadata-only binding replay and write receipts')
        finally:
            for item in report['entries']:
                if item.get('entryId'):
                    meta=vault.show(item['name'])
                    assert meta['id']==item['entryId']
                    if not meta.get('deleteAt'): vault.delete(item['name'],if_match=meta['revision'])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('fixture',type=Path);p.add_argument('--cli',type=Path)
    args=p.parse_args();run(args.fixture,args.cli)
