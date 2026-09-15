"""Local subprocess boundary check; no network, credentials, or enrollment API."""
import json
import os
from pathlib import Path
import sys
import tempfile
from dreamlake.api.hosts import _remote

if os.name != 'posix':
    raise SystemExit('This terminal/session acceptance requires POSIX')
with tempfile.TemporaryDirectory(prefix='dreamlake-ssh-session-') as directory:
    ssh=Path(directory)/'ssh'
    ssh.write_text(f'#!{sys.executable}\nimport json,os,sys\njson.load(sys.stdin)\nprint(json.dumps({{"session":os.getsid(0),"askpass":os.environ.get("SSH_ASKPASS_REQUIRE")}}))\n')
    ssh.chmod(0o700)
    previous_path=os.environ['PATH'];previous_askpass=os.environ.get('SSH_ASKPASS_REQUIRE')
    try:
        os.environ['PATH']=directory+os.pathsep+previous_path
        os.environ['SSH_ASKPASS_REQUIRE']='force'
        result=_remote(['-J','jump','host'],{'action':'probe'})
        assert result['session']!=os.getsid(0) and result['askpass']=='never'
        print('PASS SSH child session detached and askpass disabled. Local subprocess substitute only; no network access.')
    finally:
        os.environ['PATH']=previous_path
        if previous_askpass is None:os.environ.pop('SSH_ASKPASS_REQUIRE',None)
        else:os.environ['SSH_ASKPASS_REQUIRE']=previous_askpass
