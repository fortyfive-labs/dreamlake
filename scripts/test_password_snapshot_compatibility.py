#!/usr/bin/env python3
"""Read and gate migration with unmodified published clients against a synthetic fixture.

Fixture: serveKmsPolicyFixture.ts OUTPUT --password-snapshots. No cloud or SSH.
Pass actual registry installations; this runner never builds client packages.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--python', type=Path, action='append', required=True)
    parser.add_argument('--candidate-cli-dir', type=Path)
    parser.add_argument('--candidate-python-src', type=Path)
    args = parser.parse_args()
    assert args.fixture.stat().st_mode & 0o077 == 0
    data = json.loads(args.fixture.read_text())
    assert data['url'].startswith('http://127.0.0.1:')
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'DREAMLAKE_REMOTE', 'DREAMLAKE_API_KEY')}
    cli_env = {**env, 'DREAMLAKE_REMOTE': data['url'], 'DREAMLAKE_API_KEY': data['aliceToken']}
    with httpx.Client(base_url=data['url'], headers={'Authorization': 'Bearer '+data['aliceToken']}) as http:
        preview = http.post('/v1/vault/kms/preview', json={'prefix': 'alice/snapshots', 'keyRef': 'research'}).json()
        assert preview['passwordSnapshotCount'] == 2 and preview['totalRetainedRecordCount'] == 4
        assert preview['retainedRecordSchemaVersion'] == 2
        for route, body, headers in [('/v1/vault/kms/migrations', {'prefix':'alice/snapshots','keyRef':'research'}, {'Idempotency-Key':'legacy-code-check'}), ('/v1/vault/kms/migrations/legacy-started/resume', {}, {})]:
            rejected=http.post(route,json=body,headers=headers)
            assert rejected.status_code==409 and rejected.json()['error']['code']=='KMS_RETAINED_RECORD_UPGRADE_REQUIRED'
        before = http.get('/v1/vault/kms/operations/legacy-started').json()
        assert before['migrated'] == 0 and before['requiredRetainedRecordSchemaVersion'] == 2
        def cli(*argv, success=True):
            result = subprocess.run([str(args.cli), *argv], env=cli_env, capture_output=True, text=True, timeout=30)
            assert (result.returncode == 0) == success
            assert 'SYNTHETIC_' not in result.stdout+result.stderr
            return result.stdout
        version = cli('--version').strip()
        visible = json.loads(cli('vault', 'kms', 'preview', '-p', 'alice/snapshots', '--key', 'research'))
        assert 'passwordSnapshotCount' not in visible  # Historical parser drops unknown fields.
        cli('vault', 'kms', 'migrate', '-p', 'alice/snapshots', '--key', 'research', '--request-id', 'legacy-new', success=False)
        cli('vault', 'kms', 'resume', 'legacy-started', success=False)
        script = '''import json,sys,httpx,importlib.metadata
from dreamlake.vault import Vault,VaultError
p=json.load(open(sys.argv[1]))
with httpx.Client(base_url=p['url'],headers={'Authorization':'Bearer '+p['aliceToken']}) as h:
 v=Vault(h)
 r=v.kms.preview(prefix='alice/snapshots',key_ref='research')
 assert 'passwordSnapshotCount' not in r
 for call in [lambda:v.kms.migrate(prefix='alice/snapshots',key_ref='research',request_id='legacy-python'),lambda:v.kms.resume(request_id='legacy-started')]:
  try:call();raise AssertionError('Old client migrated snapshots')
  except VaultError as e:assert getattr(e,'status',None)==409
 print(importlib.metadata.version('dreamlake'))
'''
        versions=[]
        for python in args.python:
            result=subprocess.run([str(python),'-c',script,str(args.fixture)],env=env,capture_output=True,text=True,timeout=30)
            if result.returncode:raise RuntimeError('Published Python compatibility assertion failed')
            versions.append(result.stdout.strip())
        after=http.get('/v1/vault/kms/operations/legacy-started').json()
        assert before == after
        for request_id in ('legacy-new','legacy-python'):
            assert http.get('/v1/vault/kms/operations/'+request_id).status_code==404
        if args.candidate_cli_dir:
            result=subprocess.run(['node','--import','tsx','src/cli/index.ts','vault','kms','resume','legacy-started','--limit','1'],cwd=args.candidate_cli_dir,env=cli_env,capture_output=True,text=True,timeout=30)
            assert result.returncode==0
            assert json.loads(result.stdout)['requiredRetainedRecordSchemaVersion']==2
        if args.candidate_python_src:
            script_new='''import sys,json,httpx
sys.path.insert(0,sys.argv[2])
from dreamlake.vault import Vault
p=json.load(open(sys.argv[1]))
with httpx.Client(base_url=p['url'],headers={'Authorization':'Bearer '+p['aliceToken']}) as h:
 v=Vault(h)
 preview=v.kms.preview(prefix='alice/snapshots',key_ref='research')
 assert preview['passwordSnapshotCount']==2 and preview['totalRetainedRecordCount']==4
 assert preview['retainedRecordSchemaVersion']==2
 result=v.kms.resume(request_id='legacy-started')
 assert result['state']=='completed' and result['requiredRetainedRecordSchemaVersion']==2
'''
            result=subprocess.run([str(args.python[-1]),'-c',script_new,str(args.fixture),str(args.candidate_python_src)],env=env,capture_output=True,text=True,timeout=30)
            assert result.returncode==0
    print(json.dumps({'cli':version,'python':versions,'preview':'accepted; historical parsers omit snapshot count','publishedStartResume':'rejected409; no migration progress', 'candidateResume':bool(args.candidate_cli_dir and args.candidate_python_src),'scope':'synthetic loopback HTTP/native Mongo; no cloud or SSH'}))


if __name__ == '__main__':
    main()
