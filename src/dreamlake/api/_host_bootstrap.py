import base64, fcntl, pwd, hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile, urllib.request

def run(args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
def prerequisite_error(code):
    print(json.dumps({'code': code}))
    sys.exit(1)
def check_prerequisites():
    if not shutil.which('openssl'):
        prerequisite_error('openssl_missing')
    try:
        run(['systemctl', '--user', 'show-environment'])
    except Exception:
        prerequisite_error('systemd_user_unavailable')
    try:
        linger = run(['loginctl', 'show-user', pwd.getpwuid(os.getuid()).pw_name, '-p', 'Linger', '--value']).stdout.strip()
    except Exception:
        prerequisite_error('linger_unavailable')
    if linger != b'yes':
        prerequisite_error('linger_required')
def write(path, value):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as f: f.write(value)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def main():
    p = json.load(sys.stdin)
    # Check before identity creation or requesting an enrollment grant.
    check_prerequisites()
    home = pathlib.Path.home()
    root = home / '.local/state/dreamlake/hosts' / hashlib.sha256(p['name'].encode()).hexdigest()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    with open(root / 'lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        key = root / 'identity.key'
        if not key.exists(): write(key, base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))
        os.chmod(key, 0o600)
        raw = base64.urlsafe_b64decode(key.read_text().strip() + '==')
        if len(raw) != 32: raise RuntimeError('identity format')
        public = run(['openssl','pkey','-inform','DER','-pubout','-outform','DER'], input=bytes.fromhex('302e020100300506032b657004220420') + raw).stdout
        if len(public) != 44 or public[:12] != bytes.fromhex('302a300506032b6570032100'): raise RuntimeError('public format')
        public_key = base64.urlsafe_b64encode(public[12:]).decode().rstrip('=')
        if p['action'] == 'probe':
            print(json.dumps({'unixUser':pwd.getpwuid(os.getuid()).pw_name,'publicKey':public_key}))
            return
        if public_key != p['publicKey']: raise RuntimeError('identity changed')
        # Never fall back to a privileged system unit or an unsupervised daemon.
        binary = shutil.which('nymph')
        if not binary:
            bindir = home / '.local/bin'
            bindir.mkdir(parents=True, exist_ok=True)
            binary = str(bindir / 'nymph')
            if not pathlib.Path(binary).exists():
                with urllib.request.urlopen('https://dreamlake.ai/dreamlake/nymph/install.sh', timeout=30) as response:
                    installer = response.read()
                run(['bash','-s','--','--dest',str(bindir),'--version',p['version']], input=installer)
        grant = root / 'enroll-token'
        if p.get('token'): write(grant, p['token'])
        q = json.dumps
        config = '\n'.join([
            '[server]', 'url = ' + q(p['controlPlaneUrl']), 'namespace = ' + q(p['namespace']),
            '[daemon]', 'machine_id = ' + q(p['machineId']), 'label = ' + q(p['name']),
            '[runtime]', 'keep_alive_s = -1', 'workdir = ' + q(str(root / 'work')), 'runners = ["process"]', 'default_runner = "process"',
            '[identity]', 'enabled = true', 'key_file = ' + q(str(key)), 'enroll_token_file = ' + q(str(grant)),
        ]) + '\n'
        (root / 'work').mkdir(exist_ok=True)
        config_file = root / 'nymph.toml'
        changed = not config_file.exists() or config_file.read_text() != config
        write(config_file, config)
        # JSON string escaping covers quoted systemd arguments; percent must be escaped separately.
        unit_quote = lambda s: q(s.replace('%','%%'))
        unit_name = 'dreamlake-host-' + hashlib.sha256(p['name'].encode()).hexdigest()[:24] + '.service'
        unit_dir = home / '.config/systemd/user'
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit = '[Unit]\nDescription=DreamLake nymph host\n[Service]\nExecStart=' + unit_quote(binary) + ' --config ' + unit_quote(str(config_file)) + '\nRestart=on-failure\nRestartSec=3\n[Install]\nWantedBy=default.target\n'
        unit_file = unit_dir / unit_name
        changed = changed or not unit_file.exists() or unit_file.read_text() != unit
        write(unit_file, unit)
        run(['systemctl','--user','daemon-reload'])
        run(['systemctl','--user','enable',unit_name])
        run(['systemctl','--user','restart' if changed else 'start',unit_name])
        print(json.dumps({'started':True}))
try:
    main()
except Exception:
    # Never forward child stdout/stderr or payloads: these can contain grants.
    print(json.dumps({'error':'Remote bootstrap failed. Verify Python 3, OpenSSL, systemd user manager, user linger, and nymph installation access.'}))
    sys.exit(1)
