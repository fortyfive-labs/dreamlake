"""Real loopback HTTP/Mongo/CLI/Python recovery test; synthetic values only."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import httpx
from dreamlake.vault import Vault, VaultError, VaultWriteError


def run(fixture, cli_checkout):
    if not __debug__:
        raise ValueError('Assertions required')
    if fixture.is_symlink() or not fixture.is_file() or fixture.stat().st_mode & 0o077:
        raise ValueError('Private fixture file required')
    settings = json.loads(fixture.read_text())
    if urlparse(settings['url']).hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('Loopback fixture required')
    unique = secrets.token_hex(8)
    secret = 'SYNTHETIC_WRITE_RECOVERY_' + unique + '\n'
    headers = {'Authorization': 'Bearer ' + settings['aliceToken']}
    writes = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_PUT(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            with httpx.Client() as upstream:
                response = upstream.put(settings['url'] + self.path, content=body, headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','content-length')})
                response.read()
            writes.append(response.status_code)
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
    proxy = ThreadingHTTPServer(('127.0.0.1',0), Proxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    proxy_url = f'http://127.0.0.1:{proxy.server_port}'
    def cli(*args, value=None, remote=None, user='alice'):
        result = subprocess.run(['node','--import','tsx','src/cli/index.ts','vault',*args], cwd=cli_checkout, env={**os.environ,'DREAMLAKE_REMOTE':remote or settings['url'],'DREAMLAKE_API_KEY':settings[user+'Token']},input=value,capture_output=True,text=True,timeout=45)
        assert secret.strip() not in result.stdout + result.stderr, 'Secret appeared in CLI diagnostics'
        return result
    try:
        with httpx.Client(base_url=settings['url'],headers=headers) as http, httpx.Client(base_url=proxy_url,headers=headers) as lossy, httpx.Client(base_url=settings['url'],headers={'Authorization':'Bearer '+settings['bobToken']}) as bob_http:
            vault, bob = Vault(http), Vault(bob_http)
            name = 'alice/python-recovery-' + unique
            try:
                Vault(lossy).add(name, secret, request_id='python-'+unique)
            except VaultWriteError as error:
                assert error.request_id == 'python-'+unique and secret not in str(error)
            else:
                raise AssertionError('Expected lost response')
            assert writes == [200]
            assert vault.write_status(request_id='python-'+unique)['entry']['revision'] == 1
            replay = vault.add(name, secret, request_id='python-'+unique)
            assert replay['revision'] == 1 and replay['replayed'] is True
            vault.add(name, 'SYNTHETIC_NEW_VALUE', if_match=1, request_id='new-'+unique)
            assert vault.add(name, secret, request_id='python-'+unique)['revision'] == 1
            assert vault.show(name)['revision'] == 2
            for action in (
                lambda: vault.add(name,'DIFFERENT_SYNTHETIC',request_id='python-'+unique),
                lambda: vault.add(name,secret,if_match=1,request_id='python-'+unique),
                lambda: vault.add(name,secret,if_match=1,request_id='stale-'+unique),
                lambda: bob.write_status(request_id='python-'+unique),
                lambda: bob.add(name,secret,request_id='python-'+unique),
            ):
                try: action()
                except VaultError: pass
                else: raise AssertionError('Expected denial/conflict')
            assert vault.show(name)['revision'] == 2
            cli_name = 'alice/cli-recovery-'+unique
            result = cli('add','-n',cli_name,'--stdin','--request-id','cli-'+unique,value=secret,remote=proxy_url)
            assert result.returncode != 0 and 'cli-'+unique in result.stderr
            assert writes == [200,200]
            status = cli('write-status','--request-id','cli-'+unique)
            assert status.returncode == 0 and json.loads(status.stdout)['entry']['revision'] == 1
            replay = cli('add','-n',cli_name,'--stdin','--request-id','cli-'+unique,value=secret)
            assert replay.returncode == 0 and json.loads(replay.stdout)['replayed'] is True
            assert cli('write-status','--request-id','cli-'+unique,user='bob').returncode != 0
            assert cli('add','-n',cli_name,'--stdin','--request-id','cli-'+unique,value='SYNTHETIC_DIFFERENT').returncode != 0
            assert vault.show(cli_name)['revision'] == 1 and vault.get(cli_name) == secret
            assert vault.write_status(request_id='cli-'+unique)['entry']['revision'] == 1
            assert cli('write-status','--request-id','python-'+unique).returncode == 0
            assert cli('write-status','--request-id','missing-'+unique).returncode != 0
    finally:
        proxy.shutdown(); proxy.server_close(); thread.join()
    print('PASS: real HTTP/Mongo CLI/Python lost-response recovery, replay, conflicts, tenant denial, redaction and byte preservation')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--cli-checkout',type=Path,required=True)
    args = parser.parse_args()
    run(args.fixture,args.cli_checkout)
