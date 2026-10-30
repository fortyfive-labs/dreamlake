"""The callable facade: `dl.notes("ns/id")` / `dl.notes.list(namespace=...)`.

Wire shape is asserted against a fake transport, as in test_notes_v2. The
facade adds no mutation engine, so what is checked here is the adapter: named
fields, required mode, snapshot-vs-revision typing, and that the bytes sent are
the ones `Note.append(..., at=snapshot)` already sends.
"""
import hashlib
import inspect
import json

import httpx
import pytest

import dreamlake
from dreamlake.api._client import DreamLakeClient
from dreamlake.api._notes_facade import (
    FocusedRead,
    NoteHandle,
    Notes,
    ReadingViewRefused,
    RevisionExpected,
    notes,
)
from dreamlake.api.notes import Note, NoteNotFound, NoteRef, NoteSnapshot, PatchReceipt

NS = 'fortyfive'
ID = '6ae3f318608512b4ba34860c'
REV = 'rtc:' + 'c9' * 32
NEXT = 'rtc:' + 'ab' * 32


def snap(text, revision=REV, note=ID):
    return NoteSnapshot(note, text, 'sha256:' + hashlib.sha256(text.encode()).hexdigest(), revision)


def client(handler):
    return DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(handler))


def never(req):
    raise AssertionError(f'unexpected request {req.method} {req.url}')


def ack(req):
    data = json.loads(req.content)
    return httpx.Response(200, json=dict(note=ID, mode=data['mode'], baseRevision=data['baseRevision'],
                                         hash=snap('x').hash, revision=NEXT), request=req)


# ── shape of `notes` ─────────────────────────────────────────────────────────

def test_module_level_notes_is_callable_with_list_and_nothing_else():
    assert isinstance(dreamlake.notes, Notes) and dreamlake.notes is notes
    assert callable(notes) and callable(notes.list)
    public = {n for n in dir(Notes) if not n.startswith('_')}
    assert public == {'list'}


def test_open_is_local_and_synchronous():
    handle = client(never).notes(f'{NS}/{ID}')
    assert isinstance(handle, NoteHandle)
    assert handle.namespace == NS and handle.ref == f'{NS}/{ID}' and handle.id == ID
    # An unbound facade also opens without touching credentials or the network.
    assert notes(f'{NS}/{ID}').ref == f'{NS}/{ID}'


@pytest.mark.parametrize('ref', ['', ID, f'{NS}/', f'/{ID}', f'{NS}/{ID}/extra', 42])
def test_reference_must_be_namespace_slash_note(ref):
    with pytest.raises(ValueError):
        notes(ref)


# ── read ─────────────────────────────────────────────────────────────────────

def test_reference_with_a_trailing_newline_is_not_an_id():
    # Same `$`-before-newline trap as command ids: it is a slug lookup, not an id.
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={'notes': []}, request=req)
    with pytest.raises(NoteNotFound):
        client(handler).notes(f'{NS}/{ID}\n').id
    assert [r.url.path for r in calls] == [f'/namespaces/{NS}/notes']


def test_read_is_the_v2_source_read():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=snap('Hello world.\n').to_dict(), request=req)
    got = client(handler).notes(f'{NS}/{ID}').read()
    assert isinstance(got, NoteSnapshot) and got == snap('Hello world.\n')
    (req,) = calls
    assert req.method == 'GET' and req.url.path == f'/namespaces/{NS}/notes/{ID}/body'
    assert dict(req.url.params) == {'contract': 'v2'} and 'if-match' not in req.headers


@pytest.mark.parametrize('at', [REV, snap('', REV), {'revision': REV, 'note': ID, 'content': '', 'hash': 'x'}])
def test_read_at_retained_revision(at):
    def handler(req):
        assert dict(req.url.params) == {'contract': 'v2', 'at': REV}
        return httpx.Response(200, json=snap('old').to_dict(), request=req)
    assert client(handler).notes(f'{NS}/{ID}').read(at=at).revision == REV


@pytest.mark.parametrize('bad', ['sha256:' + 'a' * 64, 'a' * 64, '"' + 'a' * 64 + '"'])
def test_read_refuses_hash_where_revision_expected(bad):
    with pytest.raises(RevisionExpected):
        client(never).notes(f'{NS}/{ID}').read(at=bad)


@pytest.mark.parametrize('bad', ['', 'rtc:', 'original:4', 'rtc:a\nb', 7])
def test_read_refuses_non_revision_strings(bad):
    with pytest.raises(ValueError):
        client(never).notes(f'{NS}/{ID}').read(at=bad)


def test_read_view_is_source_only_in_this_phase():
    with pytest.raises(ValueError):
        client(never).notes(f'{NS}/{ID}').read(view='mapped')


def test_slug_resolves_to_id_on_first_use_then_reads_by_id():
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.path == f'/namespaces/{NS}/notes':
            return httpx.Response(200, json={'notes': [{'id': ID, 'slug': 'design-doc', 'name': 'Design'}]}, request=req)
        return httpx.Response(200, json=snap('s').to_dict(), request=req)
    handle = client(handler).notes(f'{NS}/design-doc')
    assert calls == []
    assert handle.read().note == ID and handle.id == ID
    assert [r.url.path for r in calls] == [f'/namespaces/{NS}/notes', f'/namespaces/{NS}/notes/{ID}/body']


# ── append ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('mode', ['exact', 'merge'])
def test_append_sends_what_note_append_sends(mode):
    original = snap('Hello world.')
    calls = []
    def handler(req):
        calls.append(req)
        return ack(req)
    receipt = client(handler).notes(f'{NS}/{ID}').append(text='Hi', at=original, mode=mode)
    assert isinstance(receipt, PatchReceipt) and receipt.mode == mode and receipt.revision == NEXT
    (req,) = calls
    assert req.method == 'PATCH' and req.url.path == f'/namespaces/{NS}/notes/{ID}/body'
    assert json.loads(req.content) == {'format': 'inline-dff', 'patch': '@@ chars 12:12 @@\n~ {+\\nHi+}\n',
                                       'baseRevision': REV, 'mode': mode}
    # Same bytes as the existing method: the facade is an adapter, not an engine.
    def mirror(req):
        assert json.loads(req.content) == json.loads(calls[0].content)
        return ack(req)
    Note(ID, namespace=NS, client=client(mirror)).append('Hi', at=original, exact=(mode == 'exact'))


def test_append_mode_is_required_and_has_no_default():
    handle = client(never).notes(f'{NS}/{ID}')
    with pytest.raises(TypeError):
        handle.append(text='Hi', at=snap(''))
    for bad in (None, 'fuzzy', True, 'EXACT'):
        with pytest.raises(ValueError):
            handle.append(text='Hi', at=snap(''), mode=bad)


def test_append_at_bare_revision_reads_that_retained_source_first():
    original = snap('Hello world.\n')
    calls = []
    def handler(req):
        calls.append(req)
        if req.method == 'GET':
            assert dict(req.url.params) == {'contract': 'v2', 'at': REV}
            return httpx.Response(200, json=original.to_dict(), request=req)
        assert json.loads(req.content)['patch'] == '@@ chars 13:13 @@\n~ {+Hi+}\n'
        return ack(req)
    client(handler).notes(f'{NS}/{ID}').append(text='Hi', at=REV, mode='exact')
    assert [r.method for r in calls] == ['GET', 'PATCH']


def test_append_accepts_a_snapshot_mapping_from_json():
    original = snap('a')
    def handler(req):
        assert json.loads(req.content)['baseRevision'] == REV
        return ack(req)
    assert client(handler).notes(f'{NS}/{ID}').append(text='b', at=original.to_dict(), mode='merge').mode == 'merge'


@pytest.mark.parametrize('at', ['sha256:' + 'a' * 64, snap('a').hash])
def test_append_refuses_hash_as_baseline(at):
    with pytest.raises(RevisionExpected):
        client(never).notes(f'{NS}/{ID}').append(text='b', at=at, mode='exact')


@pytest.mark.parametrize('bad', [
    snap('x', revision='sha256:' + 'a' * 64),
    snap('x', revision='sha256:' + 'a' * 64).to_dict(),
    snap('x', revision=snap('x').hash),
])
def test_snapshot_carrying_a_hash_as_revision_is_refused_everywhere(bad):
    # Regression: a snapshot/mapping bypassed the rtc: check a bare string gets.
    handle = client(never).notes(f'{NS}/{ID}')
    with pytest.raises(RevisionExpected):
        handle.read(at=bad)
    with pytest.raises(RevisionExpected):
        handle.append(text='b', at=bad, mode='exact')
    with pytest.raises(RevisionExpected):
        handle.write(text='b', at=bad, mode='exact')
    with pytest.raises(RevisionExpected):
        handle.patch(patch='@@ chars 0:1 @@\n~ [-x-]\n', at=bad, mode='exact')


def test_snapshot_with_a_non_rtc_revision_is_refused():
    bad = snap('x', revision='original:4')
    handle = client(never).notes(f'{NS}/{ID}')
    with pytest.raises(ValueError, match='rtc:'):
        handle.append(text='b', at=bad.to_dict(), mode='exact')
    with pytest.raises(ValueError, match='rtc:'):
        handle.read(at=bad)


@pytest.mark.parametrize('method', ['append', 'write', 'patch'])
def test_writes_refuse_reading_view_marker_before_any_request(method):
    handle = client(never).notes(f'{NS}/{ID}')
    payload = {'patch' if method == 'patch' else 'text': 'ok\n<!-- notes-reading-view page=1 -->\n'}
    with pytest.raises(ReadingViewRefused):
        getattr(handle, method)(**payload, at=snap('x'), mode='exact')


@pytest.mark.parametrize('method,payload', [('append', 'x'), ('write', 'x'), ('patch', '@@ chars 0:0 @@\n~ {+x+}\n')])
def test_write_operations_take_named_fields_only(method, payload):
    # Contract B: every operation takes named fields; the payload is not positional.
    handle = client(never).notes(f'{NS}/{ID}')
    with pytest.raises(TypeError):
        getattr(handle, method)(payload, at=snap('x'), mode='exact')
    params = inspect.signature(getattr(NoteHandle, method)).parameters
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for name, p in params.items() if name != 'self')


def test_append_snapshot_of_another_note_is_refused_before_io():
    with pytest.raises(ValueError):
        client(never).notes(f'{NS}/{ID}').append(text='b', at=snap('a', note='507f1f77bcf86cd799439011'), mode='exact')


def test_patch_read_and_write_refuse_a_snapshot_of_another_note_before_io():
    # Regression: patch only extracted the revision, so a foreign snapshot's
    # baseline was sent for this note.
    other = snap('x', note='507f1f77bcf86cd799439011')
    focused = FocusedRead(note=other.note, hash=other.hash, revision=REV, scope={'lines': [1, 1]},
                          lines=(1, 1), chars=(0, 1), content='x')
    handle = client(never).notes(f'{NS}/{ID}')
    for at in (other, other.to_dict(), focused):
        with pytest.raises(ValueError, match='belongs to note'):
            handle.patch(patch='@@ chars 0:1 @@\n~ [-x-]\n', at=at, mode='exact')
        with pytest.raises(ValueError, match='belongs to note'):
            handle.read(at=at)
        with pytest.raises(ValueError, match='belongs to note'):
            handle.write(text='y', at=at, mode='exact')


# ── provisional patch / write ────────────────────────────────────────────────

def test_patch_and_write_are_marked_provisional():
    assert NoteHandle.patch.__doc__.startswith('PROVISIONAL')
    assert NoteHandle.write.__doc__.startswith('PROVISIONAL')


def test_patch_delegates_with_required_mode():
    original = snap('Hello world.')
    body = '@@ chars 0:5 @@\n~ [-Hello-]{+Welcome+}\n'
    def handler(req):
        assert json.loads(req.content) == {'format': 'inline-dff', 'patch': body, 'baseRevision': REV, 'mode': 'exact'}
        return ack(req)
    handle = client(handler).notes(f'{NS}/{ID}')
    assert handle.patch(patch=body, at=original, mode='exact').mode == 'exact'
    assert handle.patch(patch=body, at=REV, mode='exact').base_revision == REV
    with pytest.raises(TypeError):
        handle.patch(patch=body, at=original)


def test_write_delegates_disjoint_plan_against_snapshot():
    original = snap('Draft middle Draft')
    def handler(req):
        data = json.loads(req.content)
        assert req.method == 'PATCH' and data['mode'] == 'merge' and data['baseRevision'] == REV
        assert ' middle ' not in data['patch']
        return ack(req)
    assert client(handler).notes(f'{NS}/{ID}').write(text='Done middle Done', at=original, mode='merge').revision == NEXT


# ── list ─────────────────────────────────────────────────────────────────────

def test_list_requires_namespace_and_lists_it():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={'notes': [{'id': ID, 'slug': 'a', 'name': 'A'}]}, request=req)
    got = client(handler).notes.list(namespace=NS)
    assert got == [NoteRef(id=ID, namespace=NS, name='A', slug='a')]
    (req,) = calls
    assert req.method == 'GET' and req.url.path == f'/namespaces/{NS}/notes'
    assert dict(req.url.params) == {'limit': '50', 'offset': '0'}
    with pytest.raises(TypeError):
        client(never).notes.list()
    with pytest.raises(TypeError):
        client(never).notes.list(NS)
