"""Verify existing release archives without rebuilding or importing them."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

root = Path(__file__).resolve().parents[1]
receipt = json.loads((root / 'docs/releases/0.17.0/candidate.json').read_text())
def git(*args):
    return subprocess.check_output(['git', '-C', str(root), *args])
def require(value, message):
    if not value:
        raise RuntimeError(message)

baseline = receipt['releasedBaseline']
require(git('rev-parse', 'v0.16.2').decode().strip() == baseline, 'baseline mismatch')
changed = git('diff', '--name-only', baseline, receipt['source'], '--', 'src/dreamlake').decode().splitlines()
require(sorted(changed) == ['src/dreamlake/vault.py', 'src/dreamlake/vault_tree.py'], 'unexpected runtime changes')
for name in changed:
    require(git('show', receipt['source'] + ':' + name) == git('show', receipt['reviewedTreeBytesMatch'] + ':' + name), 'reviewed tree mismatch')
source_files = git('ls-tree', '-r', '--name-only', receipt['source'], '--', 'src/dreamlake').decode().splitlines()
for name, expected in receipt['archives'].items():
    path = root / 'dist' / name
    require(path.stat().st_size == expected['size'], 'archive size mismatch')
    require(hashlib.sha256(path.read_bytes()).hexdigest() == expected['sha256'], 'archive checksum mismatch')
    if name.endswith('.whl'):
        with zipfile.ZipFile(path) as archive:
            actual = {n: archive.read(n) for n in archive.namelist() if n.startswith('dreamlake/') and not n.endswith('/')}
        prefix = 'src/'
        for source in source_files:
            require(actual.pop(source[len(prefix):], None) == git('show', receipt['source'] + ':' + source), 'wheel source mismatch')
    else:
        with tarfile.open(path) as archive:
            actual = {m.name.split('/', 1)[1]: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile() and '/src/dreamlake/' in m.name}
        for source in source_files:
            require(actual.pop(source, None) == git('show', receipt['source'] + ':' + source), 'sdist source mismatch')
    require(not actual, 'unexpected packaged runtime files')
print(json.dumps({'archivesVerified': 2, 'runtimeFiles': len(source_files), 'notesBaselineUnchanged': True}))
