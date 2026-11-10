"""Project-scoped native experiment operations; all storage authority stays on the server."""
from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path
from urllib.parse import quote


def _seg(value):
    if not isinstance(value, str) or not value:
        raise ValueError('A nonempty path segment is required')
    return quote(value, safe='')


def _query(**fields):
    return {k: ','.join(v) if isinstance(v, (list, tuple)) else v
            for k, v in fields.items() if v is not None}


class ExperimentProject:
    """One namespace/project's runs, directories, data, files, writers and jobs."""
    def __init__(self, api, namespace, project):
        self._api, self.namespace, self.project = api, namespace, project
        self.base = api._base(namespace, project)
        self.runs = ProjectRuns(self)
        self.parameters = ProjectParameters(self)
        self.logs = ProjectLogs(self)
        self.metrics = ProjectMetrics(self)
        self.tracks = ProjectTracks(self)
        self.files = ProjectFiles(self)
        self.folders = ProjectFolders(self)
        self.project_files = ProjectDocuments(self)
        self.writers = ProjectWriters(self)
        self.jobs = ProjectJobs(self)
        self.bindrs = ProjectBindrs(self)

    def _request(self, method, path, *, body=None, params=None, **kwargs):
        from dreamlake.experiments._native.utils import _serialize_value
        return self._api._request(method, self.base + path, body=_serialize_value(body),
                                  params=_query(**(params or {})), **kwargs)

    def _bytes(self, path, *, params=None):
        return self._api._send('GET', self.base + path, params=_query(**(params or {}))).content

    def _run(self, run_id):
        return '/runs/' + self._api._run_id(run_id)

    def connection(self, **options):
        from dreamlake.experiments import NativeConnection
        client = self._api._client
        return NativeConnection(client.dl_url, self.namespace, self.project,
                                lambda: client._token, transport=client._transport, **options)

    def run(self, key, **options):
        """A full recording context, including buffering, files and timestamped tracks."""
        from dreamlake.experiments import Experiment
        import os
        if any(k in os.environ for k in ('DREAMLAKE_EXPERIMENT_BROKER', 'DREAMLAKE_EXPERIMENT_BROKER_TOKEN')):
            return Experiment(prefix=f'{self.namespace}/{self.project}/{key}', **options)
        writer = {k: options.pop(k) for k in ('workload_id', 'idempotency_key', 'run_id', 'auto_create_project') if k in options}
        if 'write_protected' in options:
            options['_write_protected'] = options.pop('write_protected')
        return Experiment(prefix=f'{self.namespace}/{self.project}/{key}',
                          connection=self.connection(**writer), **options)

    def by_path(self, path):
        return self._request('GET', '/by-path/' + quote(path.strip('/'), safe='/'))

    def batch(self, action, items):
        if action not in ('trash', 'purge'):
            raise ValueError('Batch action must be trash or purge')
        return self._request('POST', '/batch/' + action, body={'items': items})

    def series(self, spec):
        return self._request('POST', '/series', body=spec)

    def aggregate_series(self, spec):
        return self._request('POST', '/aggregate-series', body=spec)

    def chart_series(self, spec):
        return self._request('POST', '/chart-series', body=spec)

    def row(self, run_id, spec):
        return self._request('POST', self._run(run_id) + '/row', body=spec)

    def schema(self):
        return self._api._request('GET', '/native-experiments/schema')

    def search_files(self, pattern, **query):
        return self._request('GET', '/files/search', params={'pattern': pattern, **query})


class _Resource:
    def __init__(self, project):
        self.p = project


class ProjectRuns(_Resource):
    def create(self, key, **metadata):
        return self.p._request('PUT', '/run-paths/' + quote(key.strip('/'), safe='/'), body=metadata)['experiment']

    def list(self, *, cursor=None, limit=50, include_archived=False):
        return self.p._api.list_runs(self.p.namespace, self.p.project, cursor=cursor, limit=limit, include_archived=include_archived)

    def get(self, run_id):
        return self.p._api.get_run(self.p.namespace, self.p.project, run_id)

    def update(self, run_id, **patch):
        return self.p._request('PATCH', self.p._run(run_id), body=patch)['experiment']

    def move(self, run_id, *, name=None, folder_id=..., folder_path=None):
        patch = {}
        if name is not None:
            patch['name'] = name
        if folder_id is not Ellipsis:
            patch['folderId'] = folder_id
        if folder_path is not None:
            patch['folderPath'] = folder_path
        return self.update(run_id, **patch)

    def protect(self, run_id, protected=True):
        return self.update(run_id, writeProtected=protected)

    def archive(self, run_id, archived=True):
        return self.p._api.archive(self.p.namespace, self.p.project, run_id, archived)

    def set_status(self, run_id, status):
        return self.p._api.set_status(self.p.namespace, self.p.project, run_id, status)

    def trash(self, run_id):
        return self.p._api.delete_run(self.p.namespace, self.p.project, run_id)

    def trashed(self, **query):
        return self.p._api.list_trash(self.p.namespace, self.p.project, **query)

    def restore(self, run_id):
        return self.p._api.restore_deleted(self.p.namespace, self.p.project, run_id)

    def purge(self, run_id):
        return self.p._request('POST', self.p._run(run_id) + '/purge')


class ProjectParameters(_Resource):
    def get(self, run_id):
        return self.p._request('GET', self.p._run(run_id) + '/parameters')['parameters']

    def set(self, run_id, data):
        return self.p._request('POST', self.p._run(run_id) + '/parameters', body={'data': data})['parameters']

    def delete(self, run_id):
        return self.p._request('DELETE', self.p._run(run_id) + '/parameters')['parameters']


class ProjectLogs(_Resource):
    def append(self, run_id, entries):
        return self.p._request('POST', self.p._run(run_id) + '/logs', body={'entries': entries})

    def list(self, run_id, *, limit=None, cursor=None, level=None, search=None,
             start_time=None, end_time=None, order_by=None, order=None):
        return self.p._request('GET', self.p._run(run_id) + '/logs', params=_query(
            limit=limit, cursor=cursor, level=level, search=search, startTime=start_time,
            endTime=end_time, orderBy=order_by, order=order))


class ProjectMetrics(_Resource):
    def _path(self, run_id, name):
        from dreamlake.experiments._native.native import _metric_name
        if not _metric_name(name):
            raise ValueError('Metric names must be a nonempty segment of at most 256 characters')
        return self.p._run(run_id) + '/metrics/' + _seg(name)

    def list(self, run_id):
        return self.p._request('GET', self.p._run(run_id) + '/metrics')['metrics']

    def append(self, run_id, name, points, **descriptor):
        return self.p._request('POST', self._path(run_id, name), body={'dataPoints': points, **descriptor})

    def rows(self, run_id, name, *, limit=1000, cursor=None):
        return self.p._request('GET', self._path(run_id, name), params=_query(limit=limit, cursor=cursor))

    def read_rows(self, run_id, name, *, limit=1000, cursor=None):
        from dreamlake.experiments._native.native import _blocks
        from dreamlake.experiments._native.metric_rows import MetricRowsPage
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('limit must be between 1 and 10000')
        rows, metric_id, next_cursor = [], None, cursor
        while len(rows) < limit:
            page = self.rows(run_id, name, limit=min(1000, limit-len(rows)), cursor=next_cursor)
            rows.extend(page['rows'])
            metric_id, next_cursor = page['metricId'], page.get('nextCursor')
            if next_cursor is None:
                break
        return MetricRowsPage(metric_id, _blocks(rows), len(rows), next_cursor is not None,
                              next_cursor, 'committed', 'storage')

    def stats(self, run_id, name):
        return self.p._request('GET', self._path(run_id, name) + '/stats')['metric']

    def update(self, run_id, name, **patch):
        return self.p._request('PATCH', self._path(run_id, name), body=patch)['metric']


class ProjectTracks(_Resource):
    def _path(self, run_id, topic):
        return self.p._run(run_id) + '/tracks/' + _seg(topic)

    def list(self, run_id, *, topic=None):
        return self.p._request('GET', self.p._run(run_id) + '/tracks', params=_query(topic=topic))

    def create(self, run_id, topic, **descriptor):
        return self.p._request('POST', self.p._run(run_id) + '/tracks', body={'topic': topic, **descriptor})

    def append(self, run_id, topic, entries):
        return self.p._request('POST', self._path(run_id, topic) + '/append_batch', body={'entries': entries})

    def read(self, run_id, topic, *, format='json', start_timestamp=None, end_timestamp=None, columns=None, limit=None, cursor=None):
        if format not in ('json', 'jsonl', 'parquet', 'mcap'):
            raise ValueError('format must be json, jsonl, parquet or mcap')
        query = _query(format=format, start_timestamp=start_timestamp, end_timestamp=end_timestamp, columns=columns, limit=limit, cursor=cursor)
        path = self._path(run_id, topic) + '/data'
        return (self.p._request('GET', path, params=query) if format == 'json'
                else self.p._bytes(path, params=query))


class ProjectFiles(_Resource):
    def __init__(self, project):
        super().__init__(project)
        self.folders = RunFileFolders(project)

    def _path(self, run_id, file_id):
        return self.p._run(run_id) + '/files/' + _seg(file_id)

    def list(self, run_id, **query):
        return self.p._request('GET', self.p._run(run_id) + '/files', params=query)

    def get(self, run_id, file_id):
        return self.p._request('GET', self._path(run_id, file_id))['file']

    def upload(self, run_id, path, **options):
        return self.p._api.upload_file(self.p.namespace, self.p.project, run_id, path, **options)

    def download(self, run_id, file_id, dest=None):
        data = self.p._bytes(self._path(run_id, file_id) + '/download')
        if dest is not None:
            Path(dest).write_bytes(data)
        return data

    def update(self, run_id, file_id, **patch):
        return self.p._request('PATCH', self._path(run_id, file_id), body=patch)['file']

    def delete(self, run_id, file_id):
        return self.p._request('DELETE', self._path(run_id, file_id))

    def media(self, run_id, file_id):
        return self.p._request('GET', self._path(run_id, file_id) + '/media')


class RunFileFolders(_Resource):
    def list(self, run_id):
        return self.p._request('GET', self.p._run(run_id) + '/folders')['folders']

    def get(self, run_id, folder_id):
        return self.p._request('GET', self.p._run(run_id) + '/folders/' + _seg(folder_id))['folder']

    def create(self, run_id, path, **metadata):
        return self.p._request('POST', self.p._run(run_id) + '/folders', body={'path': '/' + path.lstrip('/'), **metadata})['folder']

    def update(self, run_id, folder_id, **patch):
        if 'path' in patch:
            patch['path'] = '/' + patch['path'].lstrip('/')
        return self.p._request('PATCH', self.p._run(run_id) + '/folders/' + _seg(folder_id), body=patch)['folder']

    def delete(self, run_id, folder_id):
        return self.p._request('DELETE', self.p._run(run_id) + '/folders/' + _seg(folder_id))


class ProjectFolders(_Resource):
    def list(self, *, parent_id=None, cursor=None, limit=100):
        return self.p._request('GET', '/folders', params=_query(parentId=parent_id, cursor=cursor, limit=limit))

    def get(self, folder_id):
        return self.p._request('GET', '/folders/' + _seg(folder_id))['folder']

    def create(self, path, **metadata):
        return self.p._request('POST', '/folders', body={'path': path, **metadata})['folder']

    def update(self, folder_id, **patch):
        return self.p._request('PATCH', '/folders/' + _seg(folder_id), body=patch)['folder']

    def operate(self, folder_id, action):
        if action not in ('trash', 'restore', 'protect', 'unprotect', 'purge'):
            raise ValueError('Unsupported folder action')
        return self.p._request('POST', '/folders/' + _seg(folder_id) + '/' + action)

    def operation(self, operation_id):
        return self.p._request('GET', '/folder-operations/' + _seg(operation_id))['operation']

    def wait(self, operation_id, *, timeout=120, poll_interval=0.5):
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError('timeout and poll_interval must be positive')
        deadline = time.monotonic() + timeout
        while True:
            result = self.operation(operation_id)
            if result['state'] in ('done', 'failed'):
                return result
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'Folder operation {operation_id} is still pending; inspect it before another write')
            time.sleep(min(poll_interval, remaining))


class ProjectDocuments(_Resource):
    def _path(self, file_id):
        return '/project-files/' + _seg(file_id)

    def list(self, *, folder_id=None):
        return self.p._request('GET', '/project-files', params=_query(folderId=folder_id))['files']

    def get(self, file_id):
        return self.p._request('GET', self._path(file_id))['file']

    def text(self, file_id):
        return self.p._request('GET', self._path(file_id) + '/text')

    def download(self, file_id, dest=None):
        data = self.p._bytes(self._path(file_id) + '/download')
        if dest is not None:
            Path(dest).write_bytes(data)
        return data

    def media(self, file_id):
        return self.p._request('GET', self._path(file_id) + '/media')

    def upload(self, path, *, folder_id=None, name=None, description=None, tags=None, metadata=None, content_type=None):
        return self._upload('POST', '/project-files', path, name=name, folder_id=folder_id,
                            description=description, tags=tags, metadata=metadata, content_type=content_type)

    def replace(self, file_id, path, *, content_type=None):
        current = self.get(file_id)
        return self._upload('PUT', self._path(file_id) + '/content', path,
                            name=current['filename'], content_type=content_type or current['contentType'])

    def _upload(self, method, suffix, path, *, name=None, folder_id=None, description=None, tags=None, metadata=None, content_type=None):
        from ._experiment_upload import file_upload
        content, headers = file_upload(path, name=name, folder_id=folder_id, description=description,
                                       tags=tags, metadata=metadata, content_type=content_type)
        return self.p._request(method, suffix, content=content, headers=headers)['file']

    def update(self, file_id, **patch):
        return self.p._request('PATCH', self._path(file_id), body=patch)['file']

    def delete(self, file_id):
        return self.p._request('DELETE', self._path(file_id))

    def bindrs(self, file_id):
        return self.p._request('GET', self._path(file_id) + '/bindrs')['bindrs']


class ProjectWriters(_Resource):
    def create(self, idempotency_key, *, run_id=None):
        body = {'operation': 'create', 'idempotencyKey': idempotency_key}
        if run_id is not None:
            body['runId'] = run_id
        return self.p._request('POST', '/credentials', body=body)['credential']

    def get(self, credential_id):
        return self.p._request('GET', '/credentials/' + _seg(credential_id))['credential']

    def list(self, *, cursor=None, limit=None):
        return self.p._request('GET', '/credentials', params=_query(cursor=cursor, limit=limit))

    def rotate(self, credential_id, generation):
        return self.p._request('POST', '/credentials', body={'operation': 'rotate', 'credentialId': credential_id, 'expectedGeneration': generation})['credential']

    def retire(self, credential_id, generation):
        return self.p._request('POST', '/credentials', body={'operation': 'retire', 'credentialId': credential_id, 'expectedGeneration': generation})['credential']

    def session(self, credential_id, *, run_id=None):
        body = {'mode': 'write', 'workloadId': credential_id}
        if run_id is not None:
            body['runId'] = run_id
        return self.p._request('POST', '/session', body=body)


class ProjectJobs(_Resource):
    def submit(self, *, request_id, target, argv, files=None, enrollment_id=None, timeout_seconds=3600, kind='uv-run'):
        source = []
        for name, content in (files or {}).items():
            data = content.read_bytes() if isinstance(content, Path) else content.encode() if isinstance(content, str) else bytes(content)
            source.append({'path': name, 'contentBase64': base64.b64encode(data).decode(), 'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o644})
        body = {'requestId': request_id, 'target': target, 'execution': {'kind': kind, 'argv': list(argv)},
                'source': {'files': source}, 'timeoutSeconds': timeout_seconds}
        if enrollment_id is not None:
            body['enrollmentId'] = enrollment_id
        return self.p._request('POST', '/jobs', body=body)['run']

    def list(self, **query):
        return self.p._request('GET', '/jobs', params=query)

    def summary(self, job_id):
        return self.p._request('GET', '/jobs/' + _seg(job_id) + '/summary')

    def status(self, job_id):
        return self.summary(job_id)['run']

    def cancel(self, job_id):
        self.summary(job_id)
        return self.p._api._request('POST', f'/namespaces/{_seg(self.p.namespace)}/runs/{_seg(job_id)}/cancel')['run']

    def logs(self, job_id, *, cursor=None):
        self.summary(job_id)
        return self.p._api._request('GET', f'/namespaces/{_seg(self.p.namespace)}/runs/{_seg(job_id)}/logs', params=_query(cursor=cursor))


class ProjectBindrs(_Resource):
    def create(self, name, **metadata):
        return self.p._api._client.create_bindr(self.p.namespace, self.p.project, name, **metadata)

    def get(self, bindr_id):
        return self.p._api._client.get_bindr_by_id(bindr_id)

    def catalog(self):
        path = f'/namespaces/{_seg(self.p.namespace)}/projects/{_seg(self.p.project)}/bindrs'
        return self.p._api._request('GET', path)['bindrs']

    def update(self, bindr_id, **patch):
        return self.p._api._request('PATCH', '/bindrs/' + _seg(bindr_id), body=patch)['bindr']

    def delete(self, bindr_id):
        return self.p._api._request('DELETE', '/bindrs/' + _seg(bindr_id))

    def _target(self, kind, target_id, run_id=None):
        if kind == 'run':
            return self.p._run(target_id) + '/bindrs'
        if kind == 'folder':
            return '/folders/' + _seg(target_id) + '/bindrs'
        if kind in ('metric', 'file', 'parameters') and run_id is not None:
            return self.p._run(run_id) + '/bindrs'
        raise ValueError('Target is a run, folder, or a run item with run_id')

    def list(self, kind, target_id, *, run_id=None):
        query = {'itemKind': kind, 'itemId': target_id} if run_id is not None else {}
        return self.p._request('GET', self._target(kind, target_id, run_id), params=query)['bindrs']

    def attach(self, kind, target_id, *, bindr_id=None, name=None, run_id=None):
        return self._change('POST', kind, target_id, bindr_id, name, run_id)

    def detach(self, kind, target_id, *, bindr_id=None, name=None, run_id=None):
        return self._change('DELETE', kind, target_id, bindr_id, name, run_id)

    def _change(self, method, kind, target_id, bindr_id, name, run_id):
        if (bindr_id is None) == (name is None):
            raise ValueError('Select a bindr by id or name')
        body = {'bindrId': bindr_id} if bindr_id is not None else {'name': name}
        if run_id is not None:
            body['item'] = {'kind': kind, 'id': target_id}
        return self.p._request(method, self._target(kind, target_id, run_id), body=body)

    def attach_project_file(self, bindr_id, file_id):
        return self.p._api._request('POST', '/bindrs/' + _seg(bindr_id) + '/members', body={'projectFileId': file_id})

    def detach_project_file(self, bindr_id, file_id):
        return self.p._api._request('DELETE', '/bindrs/' + _seg(bindr_id) + '/members', body={'projectFileId': file_id})
