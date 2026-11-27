"""Native experiment data pinned to the worker's local reporting broker."""
import os
import re
import threading
import time
from urllib.parse import quote, urlsplit, parse_qsl

import httpx

from .native import NativeRemoteClient
from .managed import _authorization, _url, _page_url


class JobNativeConnection:
    platform_controls_status = True

    def __init__(self):
        endpoint = os.environ.get('DREAMLAKE_EXPERIMENT_BROKER', '')
        secret = os.environ.get('DREAMLAKE_EXPERIMENT_BROKER_TOKEN', '')
        try:
            p = urlsplit(endpoint)
            valid = (re.fullmatch(r'http://(?:127\.0\.0\.1|\[::1\]):[0-9]{1,5}/?', endpoint)
                     and p.port and p.path in ('', '/') and not p.query and not p.fragment)
        except ValueError:
            valid = False
        if not valid or not secret:
            raise ValueError('A loopback job broker and its credential are required')
        _authorization(secret)
        self._broker, self._broker_secret = endpoint.rstrip('/'), secret
        self._experiment_url = None
        self._pins = None
        self._client = JobNativeRemoteClient(self)
        try:
            self._client._session()
        except Exception:
            self._client.close()
            raise

    @property
    def prefix(self):
        return f'{self.namespace}/{self.project}/{self.run_key}'

    @property
    def _root(self):
        return self._client._session()['apiUrl']

    def client(self):
        return self._client

    def matches_job_environment(self):
        return (os.environ.get('DREAMLAKE_EXPERIMENT_BROKER', '').rstrip('/') == self._broker
                and os.environ.get('DREAMLAKE_EXPERIMENT_BROKER_TOKEN') == self._broker_secret)


class JobNativeRemoteClient(NativeRemoteClient):
    def __init__(self, connection):
        self.connection = connection
        self._http = httpx.Client(follow_redirects=False, trust_env=False, timeout=30)
        self._lock = threading.RLock()
        self._credential = None
        self._sessions = {}
        self._token = ''
        self._project_ready = True

    def _session(self):
        with self._lock:
            if self._credential and self._credential['expiresAt'] > time.time() + 5:
                return self._credential
            try:
                r = self._http.post(self.connection._broker + '/session',
                                    headers={'Authorization': _authorization(self.connection._broker_secret)}, json={})
                if r.status_code != 200:
                    raise ValueError('Job experiment broker denied the session')
                result = r.json()
                keys = ('namespace', 'project', 'runKey', 'runId', 'workloadId', 'instanceId', 'projectId')
                if not isinstance(result, dict) or any(not isinstance(result.get(k), str) or
                        not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', result[k]) for k in keys):
                    raise ValueError('Invalid job experiment pins')
                if result['instanceId'] != 'dreamlake-native-v1':
                    raise ValueError('Job reporting must target a native DreamLake run')
                if not all(re.fullmatch(r'[a-f0-9]{24}', result[k]) for k in ('runId', 'projectId')):
                    raise ValueError('Invalid native job identity')
                generation, expires = result.get('generation'), result.get('expiresAt')
                if type(generation) is not int or generation < 1 or type(expires) not in (int, float) or not time.time() < expires <= time.time() + 61:
                    raise ValueError('Invalid job experiment session lifetime')
                api = _url(result['apiUrl'])
                path = f'/native-experiments/ns/{quote(result["namespace"], safe="")}/projects/{quote(result["project"], safe="")}'
                if urlsplit(api).path != path:
                    raise ValueError('Invalid native job destination')
                page = _page_url(result['experimentUrl'], result['runId'])
                if parse_qsl(urlsplit(page).query, strict_parsing=True) != [('run', result['runId'])]:
                    raise ValueError('Job page must select its platform run')
                _authorization(result['token'])
                pins = tuple(result[k] for k in keys) + (generation, api, page)
                if self.connection._pins is not None and self.connection._pins != pins:
                    raise ValueError('Job experiment pins changed')
                self.connection._pins = pins
                self.connection.namespace, self.connection.project, self.connection.run_key = pins[:3]
                self.connection.run_id = result['runId']
                self.connection._experiment_url = page
                self.connection.dreamlake_url = urlsplit(api)._replace(path='', query='', fragment='').geturl()
                self.connection._path = path
                self._credential = result
                return result
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                self._credential = None
                raise ValueError('Job experiment session unavailable or changed; no login fallback') from None

    def _session_token(self):
        with self._lock:
            if self._token is None:
                self._credential = None
            self._token = self._session()['token']
            return self._token

    def _run(self, experiment_id):
        if experiment_id != self.connection.run_id:
            raise ValueError('Job experiment must use the platform run')
        return '/runs/' + experiment_id

    def _pinned_run(self):
        return self.connection.run_id

    def _send(self, method, path, action, **kwargs):
        prefix = self._run(self.connection.run_id) + '/'
        if not path.startswith(prefix) or path[len(prefix):].split('/')[0] not in ('parameters', 'logs', 'metrics', 'tracks', 'files'):
            raise ValueError('Job experiment may only access its own run data')
        return super()._send(method, path, action, **kwargs)

    def create_or_update_experiment(self, project, name, prefix=None, **kwargs):
        session = self._session()
        if project != self.connection.project or name != self.connection.run_key or prefix != self.connection.prefix:
            raise ValueError('Job experiment must use the platform run')
        if kwargs.get('write_protected') or any(kwargs.get(k) for k in ('description', 'tags', 'bindrs', 'metadata')):
            raise ValueError('Job experiment metadata is platform controlled')
        return {'experiment': {'id': session['runId'], 'name': session['runKey']}}

    def update_experiment_status(self, experiment_id, status):
        raise ValueError('Job run status is controlled by its compute receipt')

    def graphql_query(self, *args, **kwargs):
        raise ValueError('Use the job run data methods, not account GraphQL')

    def close(self):
        self._credential = None
        self._sessions.clear()
        self._http.close()
