"""Repository namespace discovery, inert YAML editing, and SDK scope precedence."""
import json
from pathlib import Path

import httpx
import pytest
from ruamel.yaml import YAML

from dreamlake.auth import Auth
from dreamlake.auth.namespace_config import namespace_config, write_local_namespace
from dreamlake.config import Config


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for key in ('DREAMLAKE_API_KEY', 'DREAMLAKE_REMOTE', 'DREAMLAKE_IDENTITY'):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: home)
    repo = home / 'workspace' / 'repo'
    nested = repo / 'nested' / 'child'
    nested.mkdir(parents=True)
    (repo / '.git').mkdir()
    global_dir = home / '.dreamlake'
    monkeypatch.setattr('dreamlake.config.config', Config(global_dir))
    def handler(request):
        if request.url.path == '/auth/me':
            return httpx.Response(200, json={'id': 'account', 'namespace': {'slug': 'personal'}})
        return httpx.Response(200, json={'data': {'myOrganizations': [
            {'slug': 'fortyfive', 'name': 'Fortyfive', 'myRole': 'owner'},
            {'slug': 'dyna-robotics', 'name': 'Dyna Robotics', 'myRole': 'member'}]}})
    auth = Auth(global_dir, transport=httpx.MockTransport(handler), cwd=nested)
    auth.login('saved-secret', profile='work')
    auth.set_default_namespace('fortyfive', scope='global')
    return home, repo, nested, auth


def test_discovery_nearest_file_git_boundary_and_home(workspace):
    home, repo, nested, _ = workspace
    (home / '.dreamrc').write_text('namespace: home-hidden\n')
    (repo.parent / '.dreamrc').write_text('namespace: parent-hidden\n')
    assert namespace_config(cwd=nested).scope == 'global'
    (repo / '.dreamrc').write_text('namespace: fortyfive\n')
    assert namespace_config(cwd=nested).namespace == 'fortyfive'
    (nested.parent / '.dreamrc').write_text('namespace: dyna-robotics\n')
    assert namespace_config(cwd=nested).path == nested.parent / '.dreamrc'
    (nested / '.git').write_text('gitdir: /a/worktree\n')
    assert namespace_config(cwd=nested).scope == 'global'
    assert namespace_config(cwd=home).scope == 'global'
    with pytest.raises(ValueError, match='home boundary'):
        namespace_config(cwd=home, scope='local', for_write=True)


def test_local_creation_targets_gitroot_or_cwd(workspace):
    home, repo, nested, auth = workspace
    with pytest.raises(ValueError, match='No local .dreamrc'):
        auth.status(scope='local')
    before = {p.name: p.read_bytes() for p in auth.config_dir.iterdir()}
    result = auth.set_default_namespace('dyna-robotics', scope='local')
    assert result['configFile'] == str(repo / '.dreamrc')
    assert result['defaultNamespace'] == 'dyna-robotics'
    assert before == {p.name: p.read_bytes() for p in auth.config_dir.iterdir()}
    outside = home / 'outside'
    outside.mkdir()
    auth.set_default_namespace('fortyfive', scope='local', cwd=outside)
    assert (outside / '.dreamrc').is_file()
    assert not (outside.parent / '.dreamrc').exists()


def test_auto_write_existing_empty_file_and_local_reset_inherit_global(workspace):
    _, repo, _, auth = workspace
    auth.set_default_namespace('dyna-robotics')
    assert not (repo / '.dreamrc').exists()
    auth.set_default_namespace('fortyfive', scope='global')
    config = repo / '.dreamrc'
    config.write_text('# repository metadata\nproject: robot\n')
    status = auth.status()
    assert status['configScope'] == 'local'
    assert status['namespaceSource'] == 'global'
    assert status['defaultNamespace'] == 'fortyfive'
    assert status['configFile'] == str(config)
    before = {p.name: p.read_bytes() for p in auth.config_dir.iterdir()}
    auth.set_default_namespace('dyna-robotics')
    assert auth.status()['namespaceSource'] == 'local'
    assert auth.status()['profileDefaultNamespace'] == 'fortyfive'
    assert before == {p.name: p.read_bytes() for p in auth.config_dir.iterdir()}
    auth.clear_default_namespace()
    assert config.is_file()
    assert '# repository metadata' in config.read_text()
    assert 'project: robot' in config.read_text()
    assert 'namespace:' not in config.read_text()
    assert auth.status()['defaultNamespace'] == 'fortyfive'
    assert auth.status(scope='local')['namespaceSource'] == 'global'


@pytest.mark.parametrize('content', ['namespace: [fortyfive]\n', 'namespace: null\n', 'namespace: bad/name\n',
                                   'namespace: fortyfive\nnamespace: dyna-robotics\n', 'namespace: [\n',
                                   '- namespace: fortyfive\n', 'namespace: !providers fortyfive\n', 'namespace: &name fortyfive\n',
                                   'name: &name fortyfive\nnamespace: *name\n', 'namespace: !!str fortyfive\n'])
def test_invalid_local_fails_and_global_bypasses(workspace, content):
    _, repo, _, auth = workspace
    config = repo / '.dreamrc'
    config.write_text(content)
    for action in (auth.status, auth.get_namespace, auth.namespaces,
                   lambda: auth.set_default_namespace('fortyfive'), auth.clear_default_namespace):
        with pytest.raises(ValueError):
            action()
    assert config.read_text() == content
    assert auth.get_namespace(scope='global') == 'fortyfive'
    auth.set_default_namespace('dyna-robotics', scope='global')
    auth.login('replacement', profile='second')  # Credentials ignore invalid repository config.
    auth.switch('work')
    assert config.read_text() == content
    assert auth.get_namespace('explicit') == 'explicit'


def test_yaml_tags_comments_fields_and_anchors_remain_inert(workspace, tmp_path):
    _, repo, _, auth = workspace
    config = repo / '.dreamrc'
    marker = tmp_path / 'must-not-exist'
    config.write_text('''# project configuration
namespace: "fortyfive" # keep namespace comment
providers: !providers
  cloud: aws # keep provider comment
settings: &settings
  region: us-west-2
copy: *settings
inert: !!python/object/apply:os.system ["touch ''' + str(marker) + '''"]
''')
    auth.set_default_namespace('dyna-robotics')
    text = config.read_text()
    assert '# project configuration' in text
    assert '# keep provider comment' in text
    assert '# keep namespace comment' in text
    assert 'providers: !providers' in text
    assert '&settings' in text and '*settings' in text
    assert not marker.exists()
    loaded = YAML(typ='rt').load(text)
    assert loaded['namespace'] == 'dyna-robotics'
    assert loaded['settings']['region'] == 'us-west-2'
    assert loaded['copy'] == loaded['settings']
    auth.clear_default_namespace()
    assert '# keep provider comment' in config.read_text()
    assert 'providers: !providers' in config.read_text()
    assert not marker.exists()


def test_comment_only_and_flow_yaml_edits_preserve_comments(workspace):
    _, repo, _, auth = workspace
    config = repo / '.dreamrc'
    config.write_text('# comment-only configuration\n\n')
    auth.set_default_namespace('fortyfive')
    assert '# comment-only configuration' in config.read_text()
    config.write_text('{namespace: fortyfive, project: "robot"} # tail\n')
    auth.set_default_namespace('dyna-robotics')
    auth.clear_default_namespace()
    assert '# tail' in config.read_text()
    assert YAML(typ='rt').load(config.read_text())['project'] == 'robot'


def test_failed_membership_does_not_create_or_change_config(workspace):
    _, repo, _, auth = workspace
    config = repo / '.dreamrc'
    with pytest.raises(ValueError, match='not available'):
        auth.set_default_namespace('unavailable', scope='local')
    assert not config.exists()
    config.write_text('namespace: fortyfive\n')
    with pytest.raises(ValueError, match='not available'):
        auth.set_default_namespace('unavailable')
    assert config.read_text() == 'namespace: fortyfive\n'


def test_local_uses_effective_environment_but_global_stays_guarded(workspace, monkeypatch):
    _, repo, _, auth = workspace
    before = {p.name: p.read_bytes() for p in auth.config_dir.iterdir()}
    monkeypatch.setenv('DREAMLAKE_API_KEY', 'environment-secret')
    monkeypatch.setenv('DREAMLAKE_REMOTE', 'https://env.invalid')
    auth.set_default_namespace('dyna-robotics', scope='local')
    assert auth.status()['defaultNamespace'] == 'dyna-robotics'
    assert auth.status()['profile'] is None
    assert before == {p.name: p.read_bytes() for p in auth.config_dir.iterdir()}
    with pytest.raises(ValueError, match='Unset'):
        auth.set_default_namespace('fortyfive', scope='global')


def test_every_sdk_namespace_resolver_applies_scope_and_explicit_precedence(workspace):
    _, repo, nested, auth = workspace
    from dreamlake import _session
    from dreamlake.api._client import DreamLakeClient
    from dreamlake.client import RemoteClient
    config = repo / '.dreamrc'
    config.write_text('namespace: dyna-robotics\nremote_url: https://not-a-credential-source\ntoken: ignored\n')
    assert _session.get_namespace(cwd=nested) == 'dyna-robotics'
    assert _session.get_namespace(cwd=nested, scope='global') == 'fortyfive'
    assert auth.get_namespace() == 'dyna-robotics'
    assert auth.token() == 'saved-secret'
    assert auth.status()['server'] == 'https://api.dreamlake.ai'
    assert auth.status()['namespace'] == 'personal'
    client = DreamLakeClient(token='saved-secret', transport=auth.transport)
    assert client.resolve_namespace(cwd=nested) == 'dyna-robotics'
    assert client.resolve_namespace(scope='global', cwd=nested) == 'fortyfive'
    remote = RemoteClient('https://api.dreamlake.ai', api_key='saved-secret')
    try:
        assert remote.resolve_namespace(cwd=nested) == 'dyna-robotics'
        config.write_text('namespace: [invalid]\n')
        assert remote.resolve_namespace('explicit', cwd=nested) == 'explicit'
        assert client.resolve_namespace('explicit', cwd=nested) == 'explicit'
        assert _session.get_namespace(namespace='explicit', cwd=nested) == 'explicit'
        with pytest.raises(ValueError):
            remote.resolve_namespace(cwd=nested)
    finally:
        remote.close()


def test_namespace_inherited_from_yaml_merge_is_not_local_selection(workspace):
    _, repo, _, auth = workspace
    config = repo / '.dreamrc'
    config.write_text('base: &base {namespace: dyna-robotics}\n<<: *base\n')
    assert auth.get_namespace() == 'fortyfive'
    assert auth.status()['namespaceSource'] == 'global'
    auth.set_default_namespace('dyna-robotics')
    assert auth.get_namespace() == 'dyna-robotics'
    auth.clear_default_namespace()
    assert auth.get_namespace() == 'fortyfive'
    assert YAML(typ='rt').load(config.read_text())['base']['namespace'] == 'dyna-robotics'


def test_status_explicit_global_default_is_distinct_from_personal_fallback(workspace):
    _, repo, _, auth = workspace
    auth.clear_default_namespace(scope='global')
    status = auth.status()
    assert status['profileDefaultNamespace'] is None
    assert status['defaultNamespace'] == 'personal'
    assert status['namespaceSource'] == 'personal'
    (repo / '.dreamrc').write_text('namespace: fortyfive\n')
    status = auth.status()
    assert status['profileDefaultNamespace'] is None
    assert status['defaultNamespace'] == 'fortyfive'
    assert status['namespaceSource'] == 'local'
