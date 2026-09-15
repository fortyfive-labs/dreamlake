"""Publish only the reviewed 0.17.0 archives; no tags or source branch changes."""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

SOURCE = '09d2af5b397f66b1f42c254a80134c77d44b43d5'
FILES = {
 'dreamlake-0.17.0-py3-none-any.whl': ('2b0d3daf2d573e178a23042a82ca59a2d0e81d7e5d56cc74fd8327333c1d1989',256900),
 'dreamlake-0.17.0.tar.gz': ('30bb2f1a49c2c8416a244ad25555a4e1f033a76ad2189d6972cb4cb9101e2b3f',214400),
}
UPLOAD = 'https://upload.pypi.org/legacy/'

class Refused(Exception): pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): raise Refused('redirect refused')

def fetch(url):
 p=urllib.parse.urlsplit(url)
 if p.scheme!='https' or p.netloc not in ('pypi.org','files.pythonhosted.org'): raise Refused('unexpected registry')
 with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(url,timeout=45) as r:return r.read()

def inventory():
 try: result=json.loads(fetch('https://pypi.org/pypi/dreamlake/0.17.0/json'))
 except urllib.error.HTTPError as e:
  if e.code==404:return {name:'absent_not_failure_proof' for name in FILES}
  raise Refused('registry unavailable') from None
 if result['info']['name']!='dreamlake' or result['info']['version']!='0.17.0':raise Refused('registry identity')
 found={}
 for name,(sha,size) in FILES.items():
  matches=[x for x in result['urls'] if x['filename']==name]
  if not matches:found[name]='absent_not_failure_proof';continue
  if len(matches)!=1:raise Refused('duplicate metadata')
  item=matches[0]
  data=fetch(item['url'])
  found[name]='exact' if item['digests']['sha256']==sha and item['size']==size and len(data)==size and hashlib.sha256(data).hexdigest()==sha else 'different'
 return found

def frozen(root):
 root=Path(root).absolute()
 if any(p.is_symlink() for p in (root,*root.parents)):raise Refused('symlink root')
 result={}
 for name,(sha,size) in FILES.items():
  path=root/'dist'/name
  if path.parent.is_symlink():raise Refused('symlink dist')
  with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:
   if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):raise Refused('nonregular archive')
   data=f.read()
  if len(data)!=size or hashlib.sha256(data).hexdigest()!=sha:raise Refused('frozen bytes differ')
  result[name]=data
 return result

def token(path,secure=False):
 path=Path(path).absolute()
 if any(p.is_symlink() for p in (path,*path.parents)):raise Refused('symlink credential path')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd) as f:
  st=os.fstat(f.fileno())
  if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1:raise Refused('credential ownership')
  if st.st_mode&0o077:
   if not secure:raise Refused('credential permissions')
   os.fchmod(f.fileno(),0o600)
  cfg=configparser.ConfigParser(interpolation=None);cfg.read_file(f)
  if cfg.get('pypi','username',fallback='__token__')!='__token__':raise Refused('token auth required')
  endpoint=cfg.get('pypi','repository',fallback=UPLOAD)
  if endpoint.rstrip('/')!=UPLOAD.rstrip('/'):raise Refused('credential endpoint mismatch')
  value=cfg.get('pypi','password')
  if not value.startswith('pypi-'):raise Refused('token format')
  return value

def upload(name,data,credential,endpoint=UPLOAD):
 with tempfile.TemporaryDirectory(prefix='dreamlake-pypi-stage-') as d:
  path=Path(d)/name;path.write_bytes(data);path.chmod(0o600)
  env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','TMPDIR')}
  env.update(UV_PUBLISH_TOKEN=credential,UV_HTTP_RETRIES='0',UV_HTTP_TIMEOUT='30',UV_NO_CONFIG='1',UV_NO_CACHE='1',NETRC=os.devnull)
  r=subprocess.run(['uv','publish','--no-config','--no-cache','--trusted-publishing','never','--keyring-provider','disabled','--no-attestations','--publish-url',endpoint,str(path)],cwd=d,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
  if r.returncode!=0:raise Refused('upload outcome unknown')

def publish(payloads,journal,credential):
 current=inventory()
 if 'different' in current.values():raise Refused('existing bytes differ')
 fd=os.open(journal,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as f:
  def record(obj):f.write(json.dumps(obj,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
  record({'source':SOURCE,'version':'0.17.0','files':FILES})
  directory=os.open(str(Path(journal).absolute().parent),os.O_RDONLY)
  try:os.fsync(directory)
  finally:os.close(directory)
  for name,data in payloads.items():
   if current[name]!='exact':
    record({'file':name,'state':'attempting'})
    try:upload(name,data,credential)
    except Exception:
     record({'file':name,'state':'unknown'});raise Refused('uncertain upload; reconcile only') from None
   if inventory()[name]!='exact':raise Refused('readback unresolved')
   record({'file':name,'state':'verified'})

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['validate','reconcile','publish']);p.add_argument('--root',required=True);p.add_argument('--journal');p.add_argument('--pypirc');p.add_argument('--secure-pypirc',action='store_true');args=p.parse_args()
 payloads=frozen(args.root)
 if args.action=='validate':print(json.dumps({'source':SOURCE,'files':FILES}));return
 if args.action=='reconcile':print(json.dumps(inventory()));return
 if not args.journal or not args.pypirc:raise Refused('protected auth and durable intent required')
 publish(payloads,args.journal,token(args.pypirc,args.secure_pypirc))
 print('Both frozen PyPI files verified; no tag or branch mutation.')

if __name__=='__main__':
 try:main()
 except Exception:
  print('Publisher refused or outcome unknown; retain intent and reconcile read-only.',file=__import__('sys').stderr);raise SystemExit(1)
