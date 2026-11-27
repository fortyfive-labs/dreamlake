"""DreamLake login selection and native job broker transport helpers."""
import os, re, json, time, threading
from pathlib import Path
from urllib.parse import urlsplit, quote, parse_qsl
import httpx

def _url(value: str) -> str:
    p = urlsplit(value)
    if (p.scheme != 'https' and not (p.scheme == 'http' and p.hostname in ('127.0.0.1', 'localhost'))) or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('DreamLake API URL must be HTTPS (HTTP is allowed only for local tests)')
    return value.rstrip('/')

def _authorization(value: str) -> str:
    # Reject control characters before httpx can include a malformed header's
    # value in an exception. Neither credential values nor headers are logged.
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._~+-]{1,16384}', value):
        raise ValueError('Authentication provider returned an invalid credential')
    return f'Bearer {value}'

def _page_url(value: str, run_id=None) -> str:
    """A page may select its exact run; service endpoints may not carry queries."""
    parsed = urlsplit(value)
    _url(parsed._replace(query='').geturl())
    if parsed.query and (not run_id or parse_qsl(parsed.query, strict_parsing=True) != [('run', run_id)]):
        raise ValueError('Invalid managed experiment page')
    return value.rstrip('/')

def _login_provider(dreamlake_url=None, config_dir=None):
    """The selected DreamLake URL and a provider of the current login token for it.

    Reads ``~/.dreamlake`` (or ``config_dir``) at each call; never writes files,
    invokes Keychain or starts a login flow. A login for one server is never
    returned for a different selected server.
    """
    directory = Path(config_dir or os.environ.get('DREAMLAKE_CONFIG_DIR') or os.environ.get('ML_DASH_MANAGED_CONFIG_DIR') or Path.home() / '.dreamlake')

    def read(name):
        try:
            value = json.loads((directory / name).read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    configured = _url(read('config.json').get('remote_url') or 'https://api.dreamlake.ai')
    selected = _url(dreamlake_url or os.environ.get('DREAMLAKE_REMOTE') or configured)

    def provider():
        current = _url(read('config.json').get('remote_url') or 'https://api.dreamlake.ai')
        environment_url = _url(os.environ.get('DREAMLAKE_REMOTE') or current)
        override = os.environ.get('DREAMLAKE_API_KEY')
        if override and selected == environment_url:
            return override
        # Re-read config as well as tokens. An environment switch cannot
        # cause an existing connection to send the new environment's token.
        token = read('tokens.json').get('dreamlake-token') if selected == current else None
        if not isinstance(token, str) or not token:
            raise ValueError('No DreamLake login for this server; run dreamlake login for the intended environment')
        return token

    return selected, provider
