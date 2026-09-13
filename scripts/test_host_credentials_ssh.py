"""Real two-hop SSH using vault-restored synthetic keys on an authorized Linux host.

Requires unprivileged sshd support and a protected isolated-vault fixture JSON:
{url, aliceToken, hostId, enrollmentId, prefix}. Run with this Python checkout.
The fixture owns its database; binding records require fixture-database cleanup.
Never use a production account or real keys. Does not install nymph or prove host
registration: it exercises the saving/binding phase and actual two-hop auth.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from urllib.parse import urlsplit

import httpx
from dreamlake.host_credentials import HostCredential, save_enrollment_credentials
from dreamlake.vault import Vault


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


def command(args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, **kwargs)


def run(fixture):
    st = fixture.lstat()
    if fixture.is_symlink() or not fixture.is_file() or st.st_mode & 0o077:
        raise ValueError('Protected fixture file required')
    config = json.loads(fixture.read_text())
    if urlsplit(config['url']).hostname not in ('localhost', '127.0.0.1'):
        raise ValueError('Use an isolated loopback fixture, optionally forwarded from an authorized backend')
    sshd = shutil.which('sshd') or '/usr/sbin/sshd'
    processes, entries, logs = [], [], []
    config['prefix'] = config['prefix'] + '/' + uuid.uuid4().hex
    home = Path.home().resolve()
    if home.stat().st_uid != os.getuid() or home.stat().st_mode & 0o022:
        raise ValueError('Fixture requires an owned home directory without group/world write permission')
    with tempfile.TemporaryDirectory(prefix='vault-host-ssh-', dir=home) as directory, httpx.Client(base_url=config['url'], headers={'Authorization':'Bearer '+config['aliceToken']}, timeout=30) as http:
        root = Path(directory)
        vault = Vault(http)
        try:
            username = pwd.getpwuid(os.getuid()).pw_name
            ports = {'target':port(), 'jump':port()}
            while ports['target'] == ports['jump']: ports['jump'] = port()
            for role in ('jump', 'target'):
                hostkey, key = root/(role+'-host'), root/(role+'-key')
                for p in (hostkey, key): command(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(p)])
                authorized = root/(role+'-authorized')
                authorized.write_bytes(Path(str(key)+'.pub').read_bytes()); authorized.chmod(0o600)
                conf = root/(role+'-sshd.conf')
                conf.write_text(f'''Port {ports[role]}
ListenAddress 127.0.0.1
HostKey {hostkey}
PidFile {root/(role+'.pid')}
AuthorizedKeysFile {authorized}
AllowUsers {username}
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
UsePAM no
StrictModes yes
PermitRootLogin prohibit-password
AllowTcpForwarding yes
PermitOpen 127.0.0.1:{ports['target']}
LogLevel DEBUG2
''')
                log = root/(role+'-sshd.log'); logs.append(log)
                proc = subprocess.Popen([sshd,'-D','-f',str(conf),'-E',str(log)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(proc)
                for _ in range(40):
                    if proc.poll() is not None: raise RuntimeError('Fixture sshd unavailable; configure authorized unprivileged sshd prerequisites')
                    try:
                        with socket.create_connection(('127.0.0.1',ports[role]),timeout=.2): break
                    except OSError: time.sleep(.1)
                else: raise RuntimeError('Fixture sshd not ready')
            selected = [HostCredential(role, f'fixture-{role}-{ports[role]}', config['prefix']+'/'+role+'-key', 'private_key', key_file=root/(role+'-key')) for role in ('target','jump')]
            result = {'enrolled':True,'host':{'id':config['hostId']},'enrollment':{'id':config['enrollmentId']}}
            report = save_enrollment_credentials(vault, result, selected, 'ssh-'+str(ports['target']))
            entries = report['entries']
            if report['status'] != 'saved': raise RuntimeError('Saving/binding failed; reconcile fixture request IDs before cleanup')
            fresh = root/'fresh'; fresh.mkdir(mode=0o700)
            for c in selected:
                saved = vault.get(name=c.entry_name)
                key = fresh/(c.role+'-key'); key.write_text(saved); key.chmod(0o600)
                if key.read_bytes() != Path(c.key_file).read_bytes(): raise RuntimeError('Restored key differs')
            # Remove original client keys to prove the login uses restored files.
            for c in selected: Path(c.key_file).unlink()
            known = fresh/'known_hosts'
            known.write_text(''.join(f"[127.0.0.1]:{ports[r]} "+(root/(r+'-host.pub')).read_text() for r in ('target','jump')))
            client = fresh/'config'
            client.write_text(f'''Host *
 User {username}
 IdentitiesOnly yes
 IdentityAgent none
 StrictHostKeyChecking yes
 UserKnownHostsFile {known}
 BatchMode yes
 PasswordAuthentication no
 KbdInteractiveAuthentication no
 ControlMaster no
 ConnectTimeout 5
Host jump
 HostName 127.0.0.1
 Port {ports['jump']}
 IdentityFile {fresh/'jump-key'}
Host target
 HostName 127.0.0.1
 Port {ports['target']}
 IdentityFile {fresh/'target-key'}
 ProxyJump jump
''')
            good = subprocess.run(['ssh','-v','-F',str(client),'target','printf vault-two-hop-ok'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if good.returncode:
                # These owned synthetic-only SSH processes handle no passwords.
                # Keep bounded stderr for actionable authentication diagnostics.
                print('Synthetic SSH stderr:', good.stderr.decode(errors='replace')[-6000:])
                for log in logs:
                    if log.exists(): print(log.name+':',log.read_text(errors='replace')[-3000:])
                raise RuntimeError('Fresh two-hop SSH failed; owned synthetic diagnostics printed')
            if good.stdout != b'vault-two-hop-ok': raise RuntimeError('Fresh two-hop login failed')
            wrong = fresh/'wrong'; command(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(wrong)])
            for role in ('target','jump'):
                original = (fresh/(role+'-key')).read_bytes()
                (fresh/(role+'-key')).write_bytes(wrong.read_bytes())
                denied = subprocess.run(['ssh','-F',str(client),'target','true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
                (fresh/(role+'-key')).write_bytes(original)
                if denied.returncode == 0: raise RuntimeError('Wrong '+role+' key unexpectedly authenticated')
            print('PASS: selected target+jump saving/binding; exact key restore; fresh two-hop SSH; independent target/jump denial')
        finally:
            for proc in processes:
                if proc.poll() is None: proc.terminate()
            for proc in processes:
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait()
            # Removal of the owned sshd processes revokes this fixture's access.
            failed = False
            for item in entries:
                try:
                    entry_id = item.get('entryId')
                    if not entry_id and item.get('status') == 'unknown':
                        entry_id = vault.write_status(request_id=item['requestId'])['entry']['id']
                    if not entry_id:
                        continue
                    meta = vault.show(item['name'])
                    if meta.get('id') != entry_id:
                        raise RuntimeError('Fixture entry identity changed')
                    if not meta.get('deleteAt'): vault.delete(item['name'], if_match=meta['revision'])
                except Exception: failed = True
            if failed: raise RuntimeError('Fixture entry retirement incomplete; retain protected fixture for reconciliation')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('fixture',type=Path)
    run(parser.parse_args().fixture)
