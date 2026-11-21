"""Repository namespace defaults. Credentials always stay in the global store."""
from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Literal

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

NamespaceScope = Literal['auto', 'local', 'global']
_NAMESPACE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


@dataclass(frozen=True)
class NamespaceConfig:
    """Selected configuration file and its literal namespace, if present."""
    scope: Literal['local', 'global']
    path: Path | None = None
    namespace: str | None = None
    original: str | None = None


def validate_namespace(value: str) -> str:
    if not isinstance(value, str) or not _NAMESPACE.fullmatch(value):
        raise ValueError('Namespace must be a literal slug such as fortyfive or dyna-robotics.')
    return value


def _yaml() -> YAML:
    # Round-trip mode derives from the safe loader. Unknown custom tags remain
    # inert YAML values; no Python class or user-provided constructor is loaded.
    parser = YAML(typ='rt')
    parser.preserve_quotes = True
    parser.allow_duplicate_keys = False
    return parser


def _read(path: Path):
    parser = _yaml()
    if not path.exists() and not path.is_symlink():
        return parser, CommentedMap(), "", None
    if not path.is_file():
        raise ValueError(f'Namespace configuration is not a readable file: {path}')
    try:
        original = path.read_text()
        document = parser.load(original)
    except (YAMLError, UnicodeError) as error:
        raise ValueError(f'Invalid YAML in namespace configuration: {path}') from error
    empty_prefix = original if document is None else ""
    if document is None:
        document = CommentedMap()
    if not isinstance(document, CommentedMap):
        raise ValueError(f'Namespace configuration must be a YAML mapping: {path}')
    for key, value in document.non_merged_items():
        if key != 'namespace':
            continue
        try:
            for scalar in (key, value):
                if getattr(getattr(scalar, 'anchor', None), 'value', None) or getattr(scalar, 'tag', None):
                    raise ValueError('Namespace keys and values cannot use YAML tags, anchors, or aliases.')
            validate_namespace(value)
        except ValueError as error:
            raise ValueError(f'Invalid namespace in {path}: use a literal namespace slug.') from error
    return parser, document, empty_prefix, original


def namespace_config(*, scope: NamespaceScope = 'auto', cwd: str | Path | None = None,
                     for_write: bool = False) -> NamespaceConfig:
    """Find nearest .dreamrc before home or across no more than one Git root.

    Global scope never inspects the working directory or repository file.
    Explicit local writes create a file at the Git root, otherwise cwd.
    """
    if scope not in ('auto', 'local', 'global'):
        raise ValueError("Namespace scope must be 'auto', 'local', or 'global'.")
    if scope == 'global':
        return NamespaceConfig('global')
    start = Path(cwd) if cwd is not None else Path.cwd()
    start = start.expanduser().resolve()
    if not start.is_dir():
        raise ValueError(f'Namespace working directory does not exist: {start}')
    home = Path.home().resolve()
    directory = start
    git_root = None
    found = None
    while directory != home:
        candidate = directory / '.dreamrc'
        if candidate.exists() or candidate.is_symlink():
            found = candidate
            break
        if (directory / '.git').exists():
            git_root = directory
            break
        if directory.parent == directory:
            break
        directory = directory.parent
    if found is not None:
        _, document, _, original = _read(found)
        return NamespaceConfig('local', found, dict(document.non_merged_items()).get('namespace'), original)
    if scope == 'auto':
        return NamespaceConfig('global')
    if not for_write:
        raise ValueError('No local .dreamrc found; use scope="global" or create a local namespace default.')
    destination = git_root or start
    if destination == home:
        raise ValueError('A local .dreamrc cannot be created at the excluded home boundary.')
    return NamespaceConfig('local', destination / '.dreamrc')


def write_local_namespace(snapshot: NamespaceConfig, namespace: str | None) -> None:
    """Preserve YAML data/comments and refuse replacement of changed files."""
    path = snapshot.path
    if snapshot.scope != 'local' or path is None:
        raise ValueError('Expected a local namespace configuration.')
    if namespace is not None:
        validate_namespace(namespace)
    if path.is_symlink():
        raise ValueError(f'Cannot replace symbolic link {path}; edit its target explicitly.')
    current = path.read_text() if path.exists() else None
    if current != snapshot.original:
        raise ValueError(f'{path} changed while selecting a namespace; retry.')
    parser, document, prefix, _ = _read(path)
    if namespace is None:
        if 'namespace' in dict(document.non_merged_items()):
            document.pop('namespace')
    else:
        document['namespace'] = namespace
    stream = StringIO()
    parser.dump(document, stream)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.')
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(prefix + ("\n" if prefix and not prefix.endswith("\n") else "") + stream.getvalue())
            os.fchmod(output.fileno(), mode)
        if (path.read_text() if path.exists() else None) != snapshot.original:
            raise ValueError(f'{path} changed while selecting a namespace; retry.')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
