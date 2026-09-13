"""Private Linux SSHD fixture; invoked only through an authorized sudo SSH channel.

Metadata is one stdin JSON line. On setup only, two following lines are fresh
synthetic passwords. Passwords and shadow hashes are never returned or recorded.
"""
import json
import os
from pathlib import Path
import pwd
import re
import signal
import select
import socket
import stat
import subprocess
import sys
import time

metadata = json.loads(sys.stdin.buffer.readline(65537))
tag = metadata['tag']
assert re.fullmatch('[a-f0-9]{12}', tag)
assert metadata['action'] in ('setup', 'inspect', 'cleanup')
assert os.geteuid() == 0
folder = Path('/var/tmp/dreamlake-password-' + tag)
users = {role: 'dlpw-' + role[0] + '-' + tag for role in ('target', 'jump')}


def account(role, absent=False, allow_missing_home=False):
    name = users[role]
    try:
        row = pwd.getpwnam(name)
    except KeyError:
        if absent:
            assert not os.path.lexists(Path('/home', name))
            return None
        raise
    assert row.pw_uid >= 1000
    assert row.pw_gecos == 'dreamlake-password-' + tag + '-' + role
    assert row.pw_dir == '/home/' + name
    home = Path(row.pw_dir)
    try:
        info = home.lstat()
    except FileNotFoundError:
        if not allow_missing_home:raise
        assert not os.path.lexists(home)
    else:
        assert stat.S_ISDIR(info.st_mode) and not home.is_symlink() and info.st_uid == row.pw_uid
    expected = metadata.get('accounts', {}).get(role)
    if expected:
        assert expected['uid'] == row.pw_uid
    return row


def owned_folder():
    info = folder.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o077 == 0
    assert not folder.is_symlink()
    return info


def proc(pid):
    root = Path('/proc', str(pid))
    try:
        assert root.stat().st_uid == 0
        fields = (root/'stat').read_text().split(') ', 1)[1].split()
        if fields[0] == 'Z':return None
        cmd = (root/'cmdline').read_bytes().split(b'\0')
        return {'start': fields[19], 'exe': os.readlink(root/'exe'), 'cmd': [x.decode() for x in cmd if x]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def stop_owned(pid, expected):
    assert hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal')
    try:
        descriptor = os.pidfd_open(pid, 0)
    except ProcessLookupError:
        return
    try:
        current = proc(pid)
        if current is None:return
        assert current['start'] == expected['start'] and current['exe'] == '/usr/sbin/sshd'
        assert str(folder/'sshd_config') in ' '.join(current['cmd']).split()
        poll = select.poll();poll.register(descriptor, select.POLLIN)
        if poll.poll(0):return
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        if not poll.poll(5000):
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            assert poll.poll(5000)
    finally:
        os.close(descriptor)


def daemon_candidates():
    statefile = folder/'state.json'
    try:
        state = json.loads(statefile.read_text())
        assert type(state['pid']) is int and state['pid'] > 1
        assert isinstance(state['process']['start'], str)
        return [(state['pid'], state['process'])]
    except (FileNotFoundError, ValueError, KeyError, TypeError, AssertionError):
        # Missing/partial receipt after spawn: discover only a root SSHD using
        # this never-reused private config; unrelated daemons cannot match it.
        candidates=[]
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():continue
            try:
                current=proc(int(path.name))
            except (OSError,AssertionError):continue
            if current and current['exe']=='/usr/sbin/sshd' and str(folder/'sshd_config') in ' '.join(current['cmd']).split():
                candidates.append((int(path.name),current))
        assert len(candidates)<=1
        return candidates


def persist_state(state):
    temporary=folder/'state.pending'
    with open(temporary,'x') as statefile:
        statefile.write(json.dumps(state));statefile.flush();os.fsync(statefile.fileno())
    os.replace(temporary,folder/'state.json')
    descriptor=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def facts():
    owned_folder()
    result = {role: {'user': users[role], 'uid': account(role).pw_uid} for role in users}
    state = json.loads((folder/'state.json').read_text())
    return {**state, 'accounts': result, 'hostPublicKey': (folder/'host-key.pub').read_text().strip()}


if metadata['action'] == 'setup':
    assert not folder.exists()
    assert Path('/run/sshd').is_dir()
    assert hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal')
    for role in users:
        assert account(role, absent=True) is None
    folder.mkdir(mode=0o700)
    try:
        for role in users:
            raw = sys.stdin.buffer.readline(514)
            assert raw.endswith(b'\n') and 32 <= len(raw) <= 513 and re.fullmatch(b'[A-Za-z0-9_-]+\n', raw)
            name = users[role]
            subprocess.run(['useradd', '--create-home', '--shell', '/bin/sh', '--comment', 'dreamlake-password-' + tag + '-' + role, name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            row = account(role)
            key = metadata['publicKey']
            assert re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,2}', key)
            ssh = Path(row.pw_dir)/'.ssh';ssh.mkdir(mode=0o700);os.chown(ssh,row.pw_uid,row.pw_gid)
            authorization = ssh/'authorized_keys';authorization.write_text(key+'\n');authorization.chmod(0o600);os.chown(authorization,row.pw_uid,row.pw_gid)
            subprocess.run(['chpasswd'], input=name.encode()+b':'+raw, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raw = b''
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(folder/'host-key')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        config = '\n'.join(['ListenAddress 127.0.0.1',f'Port {port}',f'HostKey {folder}/host-key',f'PidFile {folder}/sshd.pid',
            'AuthorizedKeysFile .ssh/authorized_keys','StrictModes yes','PasswordAuthentication yes','PubkeyAuthentication yes',
            'KbdInteractiveAuthentication no','UsePAM yes','PermitRootLogin no','PermitEmptyPasswords no',
            'AllowUsers '+' '.join(users.values()),'AllowTcpForwarding local','GatewayPorts no','X11Forwarding no',
            'PermitTunnel no','PermitUserEnvironment no','PrintMotd no','LogLevel ERROR'])+'\n'
        (folder/'sshd_config').write_text(config)
        subprocess.run(['/usr/sbin/sshd','-t','-f',str(folder/'sshd_config')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        log = open(folder/'sshd.log','wb')
        child = subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(folder/'sshd_config')],stdout=log,stderr=log,start_new_session=True)
        identity = proc(child.pid)
        assert identity is not None
        state = {'pid':child.pid,'process':identity,'port':port,'folder':str(folder)}
        persist_state(state)
        time.sleep(.2)
        assert child.poll() is None
        print(json.dumps(facts()))
    except BaseException:
        # Caller retains metadata and performs exact cleanup; never broad rm/pkill.
        raise SystemExit('Private fixture setup incomplete') from None
elif metadata['action'] == 'inspect':
    print(json.dumps(facts()))
else:
    # Only the fixture's exact master and marked users are ours. Existing system
    # daemon, configuration, users and host keys remain untouched.
    if folder.exists():
        owned_folder()
        for pid,expected in daemon_candidates():
            stop_owned(pid,expected)
    for role in users:
        row=account(role,absent=True,allow_missing_home=True)
        if row:
            uid=row.pw_uid
            # Recheck exact account immediately before each owned-UID signal.
            for sig in ('-TERM','-KILL'):
                assert account(role,allow_missing_home=True).pw_uid==uid
                subprocess.run(['pkill',sig,'-u',str(uid)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                time.sleep(.2)
            assert account(role,allow_missing_home=True).pw_uid==uid
            subprocess.run(['userdel','--remove',row.pw_name],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        assert account(role,absent=True) is None
    if folder.exists():
        owned_folder()
        allowed={'sshd_config','sshd.pid','sshd.log','host-key','host-key.pub','state.json','state.pending'}
        assert {p.name for p in folder.iterdir()}<=allowed
        for path in folder.iterdir():
            assert path.is_file() and not path.is_symlink();path.unlink()
        folder.rmdir()
    print(json.dumps({'removed':True,'accountsAbsent':2,'privateDaemonStopped':True}))
