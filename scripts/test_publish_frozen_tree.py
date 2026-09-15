import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import publish_frozen_tree as p

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
 def test_token_mode_correction_owned_only(self):
  file=self.root/'pypirc';file.write_text('[pypi]\nusername=__token__\npassword=pypi-synthetic\n');file.chmod(0o644)
  with self.assertRaises(p.Refused):p.token(file)
  self.assertEqual(p.token(file,True),'pypi-synthetic');self.assertEqual(file.stat().st_mode&0o777,0o600)
  link=self.root/'link';link.symlink_to(file)
  with self.assertRaises(p.Refused):p.token(link,True)
 def test_hardlink_auth_refused(self):
  file=self.root/'pypirc';file.write_text('');os.link(file,self.root/'alias')
  with self.assertRaises(p.Refused):p.token(file,True)
 def test_unknown_no_retry(self):
  journal=self.root/'intent';name=next(iter(p.FILES))
  with patch.object(p,'inventory',return_value={n:'absent_not_failure_proof' for n in p.FILES}),patch.object(p,'upload',side_effect=TimeoutError()) as upload:
   with self.assertRaises(p.Refused):p.publish({name:b'x'},journal,'synthetic')
   self.assertEqual(upload.call_count,1)
   with self.assertRaises(FileExistsError):p.publish({name:b'x'},journal,'synthetic')
  self.assertIn('unknown',journal.read_text())
 def test_different_no_upload(self):
  with patch.object(p,'inventory',return_value={n:'different' for n in p.FILES}),patch.object(p,'upload') as upload:
   with self.assertRaises(p.Refused):p.publish({},self.root/'intent','synthetic')
   upload.assert_not_called()
 def test_argv_env_and_cleanup(self):
  with patch.object(p.subprocess,'run') as run:
   run.return_value.returncode=0;p.upload('example.whl',b'x','pypi-synthetic')
   args,kw=run.call_args;self.assertNotIn('pypi-synthetic',str(args));self.assertEqual(kw['env']['UV_HTTP_RETRIES'],'0');self.assertFalse(Path(kw['cwd']).exists())
 def test_installed_uv_500_upload_is_not_retried(self):
  # Uses real uv plus a real loopback HTTP endpoint and a synthetic token.
  # Only enabled with an explicit path to the already frozen public wheel.
  wheel=os.environ.get('PYPI_PUBLISH_TEST_WHEEL')
  if not wheel:self.skipTest('explicit frozen wheel needed for real uv HTTP test')
  calls=[]
  class Handler(BaseHTTPRequestHandler):
   def do_GET(self):self.send_response(404);self.end_headers()
   def do_POST(self):
    calls.append(1);self.rfile.read(int(self.headers.get('Content-Length','0')));self.send_response(500);self.end_headers()
   def log_message(self,*args):pass
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with self.assertRaises(p.Refused):p.upload(Path(wheel).name,Path(wheel).read_bytes(),'pypi-synthetic','http://127.0.0.1:'+str(server.server_port)+'/legacy/')
   self.assertEqual(len(calls),1)
  finally:server.shutdown();server.server_close();thread.join(5)

if __name__=='__main__':unittest.main()
