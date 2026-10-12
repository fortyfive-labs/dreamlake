"""Wire contract tests; concurrency/identity application belongs to the server."""
import hashlib
import json
from dataclasses import FrozenInstanceError

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api.notes import (
    Note, NoteError, NoteChanged, NoteBusy, NoteNotFound, PatchFailed,
    NoteSnapshot, PatchReceipt, PatchResult,
)

NOTE = '507f1f77bcf86cd799439011'
SOURCE = 'Hello 😀\r\nworld.'
HASH = 'sha256:' + hashlib.sha256(SOURCE.encode()).hexdigest()
BASE = '"rtc:original:4"'
CURRENT = '"rtc:after-concurrent-edit:8"'
PATCH = '@@ chars 0:5 @@\n~ [-Hello-]{+Welcome+}\n'
SNAPSHOT = dict(note=NOTE, content=SOURCE, hash=HASH, revision=BASE)


def client(handler):
    return DreamLakeClient(dl_url='https://example.test', token='test',
                           transport=httpx.MockTransport(handler))


def response(req, body, status=200):
    return httpx.Response(status, json=body, request=req)


def test_read_snapshot_keeps_exact_source_and_legacy_cache():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.params['contract'] == 'v2'
        assert 'if-match' not in req.headers
        return response(req, SNAPSHOT)
    note = Note(NOTE, namespace='ns', client=client(handler))
    note._text, note._etag = 'older local cache', '"legacy-etag"'
    snapshot = note.read_snapshot()
    assert isinstance(snapshot, NoteSnapshot)
    assert snapshot.to_dict() == SNAPSHOT
    assert note._text == 'older local cache' and note.etag == '"legacy-etag"'
    with pytest.raises(FrozenInstanceError):
        snapshot.content = 'clobber'
    assert len(calls) == 1


@pytest.mark.parametrize('bad', [dict(SNAPSHOT, content='tampered'), dict(SNAPSHOT, note='other'), dict(SNAPSHOT, revision='bad\nheader'), {'text': SOURCE, 'etag': HASH}])
def test_snapshot_refuses_malformed_contract_without_adopting_cache(bad):
    note = Note(NOTE, namespace='ns', client=client(lambda req: response(req, bad)))
    note._text, note._etag = 'preserve', 'legacy'
    with pytest.raises(NoteError):
        note.read_snapshot()
    assert (note._text, note._etag) == ('preserve', 'legacy')


def test_snapshot_explicit_verification_checks_both_request_and_response():
    def handler(req):
        assert req.headers['if-match'] == BASE
        return response(req, dict(SNAPSHOT, revision=CURRENT))
    with pytest.raises(NoteChanged):
        Note(NOTE, namespace='ns', client=client(handler)).read_snapshot(if_match=BASE)


@pytest.mark.parametrize('kwargs,mode', [({}, 'merge'), ({'exact': True}, 'exact'), ({'if_match': BASE}, 'exact'), ({'exact': True, 'if_match': BASE}, 'exact')])
def test_patch_mode_original_baseline_and_shared_receipt(kwargs, mode):
    calls = []
    expected = dict(note=NOTE, mode=mode, baseRevision=BASE, hash=HASH, revision=CURRENT)
    def handler(req):
        calls.append(req)
        assert req.method == 'PATCH'
        assert json.loads(req.content) == dict(format='inline-dff', patch=PATCH, baseRevision=BASE, mode=mode)
        assert req.headers.get('if-match') == kwargs.get('if_match')
        return response(req, expected)
    note = Note(NOTE, namespace='ns', client=client(handler))
    note._etag, note._text = 'cached-but-unrelated', 'draft'
    result = note.patch(PATCH, base_revision=BASE, **kwargs)
    assert isinstance(result, PatchReceipt) and not isinstance(result, str)
    assert result.to_dict() == expected
    assert result.base_revision == BASE and result.revision == CURRENT
    assert note.etag == '"' + HASH.removeprefix('sha256:') + '"'
    assert note._text is None
    assert len(calls) == 1  # no baseline refresh after concurrent changes


def test_if_match_alone_selects_exact_and_supplies_original_baseline():
    def handler(req):
        assert json.loads(req.content)['baseRevision'] == BASE
        assert req.headers['if-match'] == BASE
        return response(req, dict(note=NOTE, mode='exact', baseRevision=BASE, hash=HASH, revision=CURRENT))
    assert Note(NOTE, namespace='ns', client=client(handler)).patch(PATCH, if_match=BASE, format='diff').mode == 'exact'


@pytest.mark.parametrize('kwargs', [{}, {'base_revision': '*'}, {'base_revision': 'bad\nheader'}, {'base_revision': BASE, 'if_match': 'different'}, {'base_revision': BASE, 'force': True}, {'base_revision': BASE, 'format': 'unknown'}, {'base_revision': BASE, 'legacy': True}, {'legacy': True, 'exact': True}])
def test_invalid_options_fail_before_any_network_request(kwargs):
    note = Note(NOTE, namespace='ns', client=object())
    note._etag = BASE  # must not silently use even a populated cache
    with pytest.raises(ValueError):
        note.patch(PATCH, **kwargs)


@pytest.mark.parametrize('status,error', [(404, NoteNotFound), (412, NoteChanged), (422, PatchFailed), (503, NoteBusy)])
def test_failures_do_not_retry_or_change_local_snapshot_or_draft(tmp_path, status, error):
    calls = []
    def handler(req):
        calls.append(req)
        return response(req, {'error': 'rejected', 'message': 'preserve original baseline'}, status)
    note = Note(NOTE, namespace='ns', client=client(handler))
    note._text, note._etag = SOURCE, 'legacy-etag'
    baseline = tmp_path / 'baseline.json'; baseline.write_text(json.dumps(SNAPSHOT))
    draft = tmp_path / 'draft.diff'; draft.write_text(PATCH)
    with pytest.raises(error):
        note.patch(draft.read_text(), base_revision=BASE)
    assert len(calls) == 1
    assert (note._text, note._etag) == (SOURCE, 'legacy-etag')
    assert json.loads(baseline.read_text()) == SNAPSHOT and draft.read_text() == PATCH


@pytest.mark.parametrize('change', [{'mode': 'exact'}, {'baseRevision': 'wrong'}, {'revision': ''}])
def test_malformed_receipt_is_not_success_or_cache_advance(change):
    result = dict(note=NOTE, mode='merge', baseRevision=BASE, hash=HASH, revision=CURRENT)
    result.update(change)
    note = Note(NOTE, namespace='ns', client=client(lambda req: response(req, result)))
    note._text, note._etag = SOURCE, 'legacy'
    with pytest.raises(NoteError):
        note.patch(PATCH, base_revision=BASE)
    assert (note._text, note._etag) == (SOURCE, 'legacy')


def test_legacy_patch_retains_string_result_and_cached_guard():
    def handler(req):
        assert json.loads(req.content) == {'diff': PATCH}
        assert req.headers['if-match'] == 'legacy-etag'
        return response(req, {'etag': 'next-etag', 'sizeBytes': 42})
    note = Note(NOTE, namespace='ns', client=client(handler)); note._etag = 'legacy-etag'
    result = note.patch(PATCH, legacy=True)
    assert isinstance(result, PatchResult) and result == 'next-etag'
    assert result.etag == 'next-etag' and result.size_bytes == 42

@pytest.mark.parametrize('source,addition,escaped', [
    ('😀x', 'a{b}[c]\\\r\n', r'\na\{b\}\[c\]\\\r\n'),
    ('a\r\n', 'b', 'b'), ('', '', ''), ('a', '', r'\n'),
])
def test_append_saved_original_single_patch(source, addition, escaped):
    original = NoteSnapshot(NOTE, source, 'sha256:' + hashlib.sha256(source.encode()).hexdigest(), BASE)
    calls = []
    def handler(req):
        calls.append(req)
        assert req.method == 'PATCH'
        data = json.loads(req.content)
        assert data['baseRevision'] == BASE
        assert data['patch'] == f'@@ chars {len(source)}:{len(source)} @@\n~ {{+{escaped}+}}\n'
        return response(req, dict(note=NOTE, mode='merge', baseRevision=BASE, hash=HASH, revision=CURRENT))
    result = Note(NOTE, namespace='ns', client=client(handler)).append(addition, at=original)
    assert isinstance(result, PatchReceipt)
    assert len(calls) == bool(escaped)


def test_append_default_reads_once_then_patches():
    calls = []
    def handler(req):
        calls.append(req.method)
        if req.method == 'GET':
            return response(req, SNAPSHOT)
        return response(req, dict(note=NOTE, mode='exact', baseRevision=BASE, hash=HASH, revision=CURRENT))
    result = Note(NOTE, namespace='ns', client=client(handler)).append('tail', exact=True)
    assert result.mode == 'exact' and calls == ['GET', 'PATCH']


@pytest.mark.parametrize('change', [dict(note='other'), dict(hash='sha256:'+'0'*64), dict(revision='bad\n')])
def test_append_invalid_original_rejected_before_io(change):
    data = dict(SNAPSHOT); data.update(change)
    with pytest.raises(ValueError):
        Note(NOTE, namespace='ns', client=object()).append('tail', at=NoteSnapshot(**data))


@pytest.mark.parametrize('opts', [{'legacy': True, 'exact': True}, {'force': True, 'exact': True}, {'legacy': True, 'at': NoteSnapshot(**SNAPSHOT)}])
def test_append_mixed_flags_rejected_before_io(opts):
    with pytest.raises(ValueError):
        Note(NOTE, namespace='ns', client=object()).append('tail', **opts)


@pytest.mark.parametrize('status', [201, 202, 204, 302, 500, 408])
def test_unknown_patch_status_preserves_cache_without_replay(status):
    calls = []
    def handler(req):
        calls.append(req)
        return response(req, {'message': 'SECRET RAW'}, status)
    note = Note(NOTE, namespace='ns', client=client(handler))
    note._text, note._etag = SOURCE, 'legacy'
    with pytest.raises(NoteError, match='write outcome unknown') as error:
        note.append('tail', at=NoteSnapshot(**SNAPSHOT))
    assert 'SECRET' not in str(error.value)
    assert len(calls) == 1 and (note._text, note._etag) == (SOURCE, 'legacy')


def test_dropped_ack_is_unknown_no_retry():
    calls = []
    def handler(req):
        calls.append(req)
        raise httpx.ReadError('SECRET', request=req)
    with pytest.raises(NoteError, match='write outcome unknown'):
        Note(NOTE, namespace='ns', client=client(handler)).append('tail', at=NoteSnapshot(**SNAPSHOT))
    assert len(calls) == 1


def test_read_retained_ack_revision_after_later_edit():
    def handler(req):
        assert req.url.params['at'] == BASE
        assert 'if-match' not in req.headers
        return response(req, SNAPSHOT)
    assert Note(NOTE, namespace='ns', client=client(handler)).read_snapshot(at=BASE).revision == BASE
    with pytest.raises(ValueError):
        Note(NOTE, namespace='ns', client=object()).read_snapshot(at=BASE, if_match=BASE)


def test_exact_empty_append_checks_server_not_fake_success():
    original = NoteSnapshot(NOTE, '', 'sha256:' + hashlib.sha256(b'').hexdigest(), BASE)
    def handler(req):
        assert json.loads(req.content)['patch'] == ''
        return response(req, {'message': 'changed'}, 412)
    with pytest.raises(NoteChanged):
        Note(NOTE, namespace='ns', client=client(handler)).append('', at=original, exact=True)


@pytest.mark.parametrize('opts', [{'legacy': True}, {'force': True}])
def test_legacy_append_cache_put_string_semantics(opts):
    def handler(req):
        assert req.method == 'PUT' and json.loads(req.content) == {'text': 'cached\ntail'}
        assert req.headers.get('if-match') == (None if opts.get('force') else 'legacy')
        return response(req, {'etag': 'next'})
    note = Note(NOTE, namespace='ns', client=client(handler))
    note._text, note._etag = 'cached', 'legacy'
    assert note.append('tail', **opts) == 'next'


def test_retained_read_rejects_different_revision():
    with pytest.raises(NoteError, match='requested retained'):
        Note(NOTE, namespace='ns', client=client(lambda req: response(req, dict(SNAPSHOT, revision=CURRENT)))).read_snapshot(at=BASE)


def test_malformed_json_ack_preserves_cache():
    def handler(req):
        return httpx.Response(200, text='SECRET malformed', request=req)
    note = Note(NOTE, namespace='ns', client=client(handler))
    note._text, note._etag = SOURCE, 'legacy'
    with pytest.raises(NoteError, match='write outcome unknown'):
        note.append('tail', at=NoteSnapshot(**SNAPSHOT))
    assert (note._text, note._etag) == (SOURCE, 'legacy')
