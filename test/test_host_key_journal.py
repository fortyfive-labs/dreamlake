import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from dreamlake.host_key_journal import (
    PHASES, append_phase, canonical_json, load_journal, publish_record, read_record,
)


def validate(value):
    assert set(value) == {'operationId', 'phase', 'name'}
    assert value['phase'] in PHASES
    return value


def genesis(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'operation.json'
    value = {'operationId': str(uuid.uuid4()), 'phase': 'prepared', 'name': '雪'}
    publish_record(path, canonical_json(value))
    return path, value


def test_chain_and_concurrent_stale_writer_adopts_winner(tmp_path):
    path, state = genesis(tmp_path)
    initial = load_journal(path, validate)
    following = dict(state, phase='replacement_saved')
    winner = append_phase(path, initial, following, validate)
    assert append_phase(path, initial, dict(following, name='loser'), validate) == winner
    assert b'\xe9\x9b\xaa' in read_record(path)
    final = winner
    for phase in PHASES[2:]:
        final = append_phase(path, final, dict(state, phase=phase), validate)
    assert load_journal(path, validate)[0]['phase'] == 'cleanup_confirmed'


def test_crash_after_link_recovers_only_known_owned_publication(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'operation.json'
    program = '''
import os, sys, uuid
from pathlib import Path
from dreamlake import host_key_journal as journal
real_link = os.link
def crash(src, dst, **kw):
    real_link(src, dst, **kw)
    os._exit(19)
journal.os.link = crash
journal.publish_record(Path(sys.argv[1]), b'{"complete":true}\\n')
'''
    result = subprocess.run([sys.executable, '-c', program, str(path)], timeout=5)
    assert result.returncode == 19
    assert path.stat().st_nlink == 2
    assert read_record(path) == b'{"complete":true}\n'
    assert path.stat().st_nlink == 1
    assert not list(tmp_path.glob('.rotation-publish-*'))
    os.link(path, tmp_path / 'external')
    with pytest.raises(ValueError, match='Shared'):
        read_record(path)


def test_real_process_publish_race_leaves_one_complete_winner(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'race'
    program = '''
import sys
from pathlib import Path
from dreamlake.host_key_journal import publish_record
print(publish_record(Path(sys.argv[1]), sys.argv[2].encode()).decode())
'''
    processes = [subprocess.Popen([sys.executable, '-c', program, str(path), str(i) * 2000],
                                 stdout=subprocess.PIPE) for i in range(4)]
    values = [process.communicate(timeout=10)[0].strip() for process in processes]
    assert all(process.returncode == 0 for process in processes)
    assert len(set(values)) == 1
    assert values[0] == read_record(path)
    assert path.stat().st_nlink == 1


def test_gap_tamper_and_invalid_utf8_fail_closed(tmp_path):
    path, state = genesis(tmp_path)
    initial = load_journal(path, validate)
    append_phase(path, initial, dict(state, phase='replacement_saved'), validate)
    folder = Path(str(path) + '.records')
    first = folder / '10-replacement_saved.json'
    first.rename(folder / '20-new_key_installed.json')
    with pytest.raises(ValueError, match='gap'):
        load_journal(path, validate)
    (folder / '20-new_key_installed.json').rename(first)
    value = json.loads(first.read_bytes())
    value['previousSha256'] = '0' * 64
    first.write_bytes(canonical_json(value))
    with pytest.raises(ValueError, match='chain'):
        load_journal(path, validate)
    path.write_bytes(b'\xff')
    with pytest.raises(UnicodeError):
        load_journal(path, validate)


def test_fifo_is_bounded_and_symlinks_rejected(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'fifo'
    os.mkfifo(path, 0o600)
    program = '''
import sys
from pathlib import Path
from dreamlake.host_key_journal import read_record
try:
    read_record(Path(sys.argv[1]))
except ValueError:
    sys.exit(0)
sys.exit(1)
'''
    assert subprocess.run([sys.executable, '-c', program, str(path)], timeout=5).returncode == 0
    target = tmp_path / 'secret'
    publish_record(target, b'complete')
    link = tmp_path / 'link'
    link.symlink_to(target)
    with pytest.raises(OSError):
        read_record(link)
