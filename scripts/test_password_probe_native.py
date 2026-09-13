"""Offline real native/npm askpass and stdin delivery against bounded fake SSH."""
import argparse
import ast
import base64
import http.server
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import threading
import uuid


def run(args):
    fake_source=ast.parse((Path(__file__).resolve().parents[1]/'test/test_host_password.py').read_text())
    fake=next(ast.literal_eval(node.value) for node in fake_source.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FAKE' for t in node.targets))
    binding_id=str(uuid.uuid4())
    binding=dict(id=binding_id,hostId='a'*24,enrollmentId='b'*24,role='target',endpoint='fixture',kind='password',entryId='entry',entryRevision=1)
    entry=dict(id='entry',name='alice/fixture',type='string',revision=1)
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps(dict(binding=binding,entry=entry)).encode())
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length',0)))
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps(dict(entries=[dict(entry,value='test-password')])).encode())
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='native-password-') as temporary:
            root=Path(temporary).resolve();root.chmod(0o700)
            fake_ssh=root/'ssh';fake_ssh.write_text('#!/usr/bin/env python3\n'+fake);fake_ssh.chmod(0o700)
            package=root/'package';package.mkdir()
            launcher=package/'launcher.cjs';shutil.copyfile(args.cli_dir/'scripts/npm-launcher.cjs',launcher)
            key=subprocess.check_output(['node','-p','process.platform+"-"+process.arch'],text=True).strip()
            platform_package=package/'node_modules'/'@dreamlake'/('dreamlake-cli-'+key);platform_package.mkdir(parents=True)
            (platform_package/'dreamlake').symlink_to(args.native.resolve())
            environment={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],'DREAMLAKE_REMOTE':f'http://127.0.0.1:{server.server_port}',
                         'DREAMLAKE_API_KEY':'x.'+base64.urlsafe_b64encode(b'{"sub":"alice"}').decode().rstrip('=')+'.x','XDG_CONFIG_HOME':str(root/'config')}
            for client in ([str(args.native.resolve())],['node',str(launcher)]):
                for case in ('verified','denied','forged','prompt'):
                    result=subprocess.run(client+['vault','verify-password','--binding-id',binding_id,'--ssh','fixture@127.0.0.1','--known-hosts','/tmp/trusted-hosts'],
                                          env={**environment,'PROBE_CASE':case},capture_output=True,timeout=30)
                    assert b'test-password' not in result.stdout+result.stderr
                    if case in ('verified','denied'):
                        assert result.returncode==(0 if case=='verified' else 1)
                        assert json.loads(result.stdout)['status']==case
                    else:assert result.returncode==1 and not result.stdout
                print('PASS '+('native' if len(client)==1 else 'npm launcher')+' private askpass password verification and negative cases',flush=True)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native',type=Path,required=True);parser.add_argument('--cli-dir',type=Path,required=True)
    run(parser.parse_args())
