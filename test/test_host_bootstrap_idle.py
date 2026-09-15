"""Execute the portable bootstrap and check persistent service configuration."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

BOOTSTRAP = Path(__file__).parents[1] / "src/dreamlake/api/_host_bootstrap.py"


def test_enrolled_service_stays_available_when_idle(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for name, body in {
        'systemctl': '#!/bin/sh\nexit 0\n',
        'loginctl': '#!/bin/sh\necho yes\n',
        'nymph': '#!/bin/sh\nexit 0\n',
    }.items():
        script = bin_dir / name
        script.write_text(body)
        script.chmod(0o700)
    env = {**os.environ, 'HOME': str(tmp_path), 'PATH': f'{bin_dir}:{os.environ["PATH"]}'}
    name = 'alice/acceptance/persistent'
    def bootstrap(payload):
        result = subprocess.run([sys.executable, str(BOOTSTRAP)],
                                input=json.dumps(payload), text=True, capture_output=True, env=env, check=True)
        return json.loads(result.stdout)
    probe = bootstrap({'action': 'probe', 'name': name})
    configure = {'action': 'configure', 'name': name, 'publicKey': probe['publicKey'],
                 'controlPlaneUrl': 'https://cp.example', 'namespace': 'alice',
                 'machineId': 'synthetic-host', 'token': 'SYNTHETIC_GRANT', 'version': 'v0.1.6'}
    assert bootstrap(configure) == {'started': True}
    digest = hashlib.sha256(name.encode()).hexdigest()
    config_path = tmp_path / '.local/state/dreamlake/hosts' / digest / 'nymph.toml'
    config = tomllib.loads(config_path.read_text())
    assert config['runtime']['keep_alive_s'] == -1
    assert config['runtime']['runners'] == ['process']
    # Retry upgrades an older generated configuration rather than retaining its idle exit.
    config_path.write_text(config_path.read_text().replace('keep_alive_s = -1\n', ''))
    assert bootstrap(configure) == {'started': True}
    assert tomllib.loads(config_path.read_text())['runtime']['keep_alive_s'] == -1
    unit = tmp_path / '.config/systemd/user' / f'dreamlake-host-{digest[:24]}.service'
    assert 'Restart=on-failure' in unit.read_text()
    assert 'WantedBy=default.target' in unit.read_text()
    assert 'SYNTHETIC_GRANT' not in config_path.read_text()
