"""Synthetic real HTTP/Mongo fixture probe; no live credentials or secret output."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
from urllib.parse import urlparse

import httpx
from dreamlake import RemoteClient
from dreamlake.vault import Vault


class LostResponse(httpx.BaseTransport):
    def __init__(self, commit):
        self.commit, self.writes = commit, 0
        self.transport = httpx.HTTPTransport()
    def handle_request(self, request):
        if request.method == 'PUT':
            self.writes += 1
            if self.commit:
                response = self.transport.handle_request(request)
                response.read(); response.close()
            raise httpx.ReadTimeout('Synthetic lost response')
        return self.transport.handle_request(request)
    def close(self):
        self.transport.close()


def run(fixture, cli_checkout=None):
    if not __debug__:
        raise ValueError('Assertions required')
    info = fixture.lstat()
    if not fixture.is_file() or fixture.is_symlink() or info.st_mode & 0o077:
        raise ValueError('Private fixture file required')
    settings = json.loads(fixture.read_text())
    if urlparse(settings['url']).hostname not in ('127.0.0.1','localhost'):
        raise ValueError('Only isolated loopback fixture allowed')
    owner = RemoteClient(settings['url'], settings['aliceToken']).vault
    other = RemoteClient(settings['url'], settings['bobToken']).vault
    prefix = 'alice/import-' + secrets.token_hex(8)
    names = [prefix + '/ssh/profiles/target', prefix + '/ssh/keys/target-1']
    names += [prefix + '/' + mode + '/ssh/profiles/target' for mode in ('committed','absent')]
    with tempfile.TemporaryDirectory(prefix='vault-import-http-') as directory:
        root = Path(directory).resolve()
        key, config = root/'id', root/'config'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)], check=True)
        original = f'Host target\n HostName example.test\n ProxyJump jump\n IdentityFile {key}\nHost jump\n IdentityFile /nonexistent-unselected\n'
        config.write_text(original); config.chmod(0o600)
        args = dict(source='ssh',prefix=prefix,config=config)
        try:
            result = owner.import_entries(**args,select=['profile:target','key:target:1'])
            assert all(entry['status']=='success' for entry in result['entries'])
            assert owner.get(names[1]) == key.read_text()
            assert owner.get(names[0])['proxyjump']=='jump'
            if cli_checkout:
                process = subprocess.run(['node','--import','tsx','src/cli/index.ts','vault','import','--ssh','-p',prefix,'--config',str(config),'--select','profile:target','--select','key:target:1','--json'],
                                         cwd=cli_checkout,env={**os.environ,'DREAMLAKE_REMOTE':settings['url'],'DREAMLAKE_API_KEY':settings['aliceToken']},capture_output=True,text=True,timeout=60)
                assert process.returncode==0
                assert all(entry['status']=='success' for entry in json.loads(process.stdout)['entries'])
                assert owner.show(names[0])['revision']==1 and owner.show(names[1])['revision']==1
                print('Canonical CLI/Python selected-import parity passed')
            denied = other.import_entries(**args, select=['profile:target'])
            assert denied['entries'][0]['status']=='failure'
            same = owner.import_entries(**args,select=['profile:target'])
            assert same['entries'][0]['status']=='success' and owner.show(names[0])['revision']==1
            config.write_text(original.replace('example.test','changed.test'))
            conflict = owner.import_entries(**args,select=['profile:target'])
            assert conflict['entries'][0]['status']=='failure'
            replaced = owner.import_entries(**args,select=['profile:target'],if_match={'profile:target':1})
            assert replaced['entries'][0]['status']=='success' and owner.show(names[0])['revision']==2
            for mode in ('committed','absent'):
                transport = LostResponse(mode=='committed')
                with httpx.Client(base_url=settings['url'],headers={'Authorization':'Bearer '+settings['aliceToken']},transport=transport) as http:
                    report = Vault(http).import_entries(source='ssh',prefix=prefix+'/'+mode,config=config,select=['profile:target'],retry=2)
                    assert transport.writes==1
                    assert report['entries'][0]['status']==('success' if mode=='committed' else 'unknown')
            print('Python import real HTTP/Mongo passed: selection, byte preservation, tenant denial, equality, pinned CAS, lost-response reconciliation/no replay')
        finally:
            for name in names:
                try:
                    owner.delete(name)
                except Exception as error:
                    if str(error)!='Vault request failed (HTTP 404)':
                        raise RuntimeError('Synthetic import cleanup incomplete') from None


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--fixture',required=True);parser.add_argument('--cli-checkout')
    try:
        args=parser.parse_args()
        run(Path(args.fixture), args.cli_checkout)
    except Exception:
        print('Python import fixture probe failed; details redacted')
        raise SystemExit(1)
