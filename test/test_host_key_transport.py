import subprocess
import sys

import pytest

from dreamlake.host_key_transport import _bounded_process, public_key_denied, ssh_config, validate_profile


def profile(tmp_path):
    return dict(host='target.example', user='fixture', port=22, knownHostsFile=str(tmp_path / 'known_hosts'),
                jump=dict(host='jump.example', user='jump', port=2200, knownHostsFile=str(tmp_path / 'known_hosts'), identityFile=str(tmp_path / 'jump-key')))


def test_actual_openssh_config_has_one_selected_identity_and_no_ambient_auth(tmp_path):
    transport = profile(tmp_path)
    config = tmp_path / 'config'
    config.write_text(ssh_config(transport, str(tmp_path / 'new-key')))
    result = subprocess.run(['ssh', '-G', '-F', str(config), 'rotation-target'], capture_output=True, text=True, check=True)
    rows = result.stdout.splitlines()
    assert [r for r in rows if r.startswith('identityfile ')] == ['identityfile ' + str(tmp_path / 'new-key')]
    for expected in ['identityagent none', 'certificatefile none', 'controlmaster false', 'stricthostkeychecking true', 'passwordauthentication no', 'proxyjump rotation-jump']:
        assert expected in rows
    assert all(r == 'controlpath none' for r in rows if r.startswith('controlpath '))
    assert all(r == 'pkcs11provider none' for r in rows if r.startswith('pkcs11provider '))
    jump = subprocess.run(['ssh', '-G', '-F', str(config), 'rotation-jump'], capture_output=True, text=True, check=True).stdout.splitlines()
    assert [r for r in jump if r.startswith('identityfile ')] == ['identityfile ' + str(tmp_path / 'jump-key')]


@pytest.mark.parametrize('field,value', [('host', 'host\nProxyCommand evil'), ('knownHostsFile', '/tmp/%h'), ('port', True)])
def test_rejects_ambient_options_and_expansion(tmp_path, field, value):
    data = profile(tmp_path)
    data[field] = value
    with pytest.raises(ValueError): validate_profile(data)
    with pytest.raises(ValueError): validate_profile({**profile(tmp_path), 'ProxyCommand':'evil'})


def test_only_selected_target_authentication_failure_is_denial(tmp_path):
    data = profile(tmp_path)
    assert public_key_denied(255, 'fixture@target.example: Permission denied (publickey).\r\n', data)
    assert not public_key_denied(255, 'jump@jump.example: Permission denied (publickey).', data)
    assert not public_key_denied(-9, 'fixture@target.example: Permission denied (publickey).', data)
    assert not public_key_denied(255, 'Host key verification failed.', data)


def test_output_and_elapsed_time_are_bounded():
    with pytest.raises(ValueError, match='limit'):
        _bounded_process([sys.executable, '-c', 'import sys;sys.stdout.write("x"*1000000)'], b'', timeout=3)
    with pytest.raises(TimeoutError):
        _bounded_process([sys.executable, '-c', 'import time;time.sleep(10)'], b'', timeout=.05)
    assert _bounded_process([sys.executable, '-c', 'import sys;print(sys.stdin.read())'], b'public') == (0, b'public\n', b'')
