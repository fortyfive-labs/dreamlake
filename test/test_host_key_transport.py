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
    local_log = 'debug1: No more authentication methods to try.\nfixture@target.example: Permission denied (publickey).\r\n'
    assert public_key_denied(255, local_log, data)
    assert not public_key_denied(255, 'Authenticated to target.example using \"publickey\".\n' + local_log, data)
    assert not public_key_denied(255, 'fixture@target.example: Permission denied (publickey).\n', data)
    assert not public_key_denied(255, 'jump@jump.example: Permission denied (publickey).', data)
    assert not public_key_denied(-9, 'fixture@target.example: Permission denied (publickey).', data)
    assert not public_key_denied(255, 'Host key verification failed.', data)


def test_output_and_elapsed_time_are_bounded():
    with pytest.raises(ValueError, match='limit'):
        _bounded_process([sys.executable, '-c', 'import sys;sys.stdout.write("x"*1000000)'], b'', timeout=3)
    with pytest.raises(TimeoutError):
        _bounded_process([sys.executable, '-c', 'import time;time.sleep(10)'], b'', timeout=.05)
    assert _bounded_process([sys.executable, '-c', 'import sys;print(sys.stdin.read())'], b'public') == (0, b'public\n', b'')


def test_parent_log_rejects_forged_remote_stderr_and_is_removed(tmp_path):
    import base64
    import json
    import os
    from dreamlake.host_key_transport import run_helper
    tmp_path.chmod(0o700)
    data = profile(tmp_path)
    data.pop('jump')
    key = tmp_path/'key';key.write_text('synthetic');key.chmod(0o600)
    fake = tmp_path/'ssh'
    public = lambda byte: 'ssh-ed25519 ' + base64.b64encode((11).to_bytes(4,'big')+b'ssh-ed25519'+(32).to_bytes(4,'big')+bytes([byte])*32).decode()
    request = dict(action='inspect',operationId='12345678-1234-1234-1234-123456789012',oldPublicKey=public(1),newPublicKey=public(2))
    denial = 'fixture@target.example: Permission denied (publickey).\n'
    good = 'debug1: No more authentication methods to try.\n' + denial
    for index, log in enumerate([good, 'Authenticated to target.example using "publickey".\n', '', 'x'*70000]):
        program = '#!'+sys.executable+'\nimport pathlib,sys\npathlib.Path(sys.argv[sys.argv.index("-E")+1]).write_text('+repr(log)+')\nsys.stderr.write('+repr(denial)+')\nsys.exit(255)\n'
        fake.write_text(program);fake.chmod(0o700)
        kwargs=dict(profile=data,selected_key=str(key),config_file=tmp_path/str(index),request=request,expect_denied=True,executable=str(fake))
        if index==0:
            assert run_helper(**kwargs)=='publickey-denied'
        else:
            with pytest.raises(ValueError,match='unconfirmed'):
                run_helper(**kwargs)
        assert not list(tmp_path.glob('.rotation-auth-*'))
