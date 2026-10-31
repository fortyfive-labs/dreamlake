"""The callable facade as the Notes API docs specify it: `dl.notes(note_id)`.

Wire shape is asserted against a fake transport, as in test_notes_v2. The
facade adds no mutation engine, so what is checked here is the adapter: IDs
primary, keyword-only fields, snapshot-vs-revision typing, outcome categories,
and that the bytes sent are the ones `Note.patch` already sends.
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
    MappedRead,
    NoteCreatedWithoutSource,
    NoteHandle,
    Notes,
    ReadingViewRefused,
    RevisionExpected,
    SnapshotRequired,
    notes,
)
from dreamlake.api.notes import (
    CommandIntentMismatchError,
    Note,
    NoteBusy,
    NoteChanged,
    NoteError,
    NoteNotFound,
    NoteRef,
    NoteSnapshot,
    PatchReceipt,
    UncertainOutcomeError,
    WriteOutcomeUnknown,
)

NS = 'fortyfive'
ID = '6ae3f318608512b4ba34860c'
OTHER = '507f1f77bcf86cd799439011'
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


def identity(req, note=ID, namespace=NS):
    return httpx.Response(200, json={'id': note, 'namespaceSlug': namespace, 'name': 'A', 'slug': 'a'}, request=req)


# ── shape of `notes` ─────────────────────────────────────────────────────────

def test_module_level_notes_is_callable_with_collection_operations_only():
    assert isinstance(dreamlake.notes, Notes) and dreamlake.notes is notes
    assert callable(notes) and callable(notes.list) and callable(notes.create)
    public = {n for n in dir(Notes) if not n.startswith('_')}
    assert public == {'list', 'create'}


def test_open_by_stable_id_is_local_and_synchronous():
    handle = client(never).notes(ID)
    assert isinstance(handle, NoteHandle)
    assert handle.id == ID and handle.ref == ID
    assert notes(ID).ref == ID  # unbound: no credentials read, no request


def test_id_handle_resolves_its_namespace_once_on_first_use():
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.path == f'/notes/{ID}':
            return identity(req)
        return httpx.Response(200, json=snap('s').to_dict(), request=req)
    handle = client(handler).notes(ID)
    assert handle.namespace == NS
    assert handle.read().content == 's' and handle.read().content == 's'
    assert [r.url.path for r in calls] == [f'/notes/{ID}', f'/namespaces/{NS}/notes/{ID}/body', f'/namespaces/{NS}/notes/{ID}/body']


def test_explicit_namespace_asserts_source_context_without_a_lookup():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=snap('s').to_dict(), request=req)
    for handle in (client(handler).notes(ID, namespace=NS), client(handler).notes(f'{NS}/{ID}')):
        assert handle.namespace == NS and handle.id == ID and handle.ref == f'{NS}/{ID}'
        handle.read()
    assert [r.url.path for r in calls] == [f'/namespaces/{NS}/notes/{ID}/body'] * 2


@pytest.mark.parametrize('ref,kwargs', [('design-doc', {}), ('', {}), (f'{NS}/', {}), (f'/{ID}', {}),
                                        (f'{NS}/{ID}/extra', {}), (42, {}), (f'{NS}/{ID}', {'namespace': NS}),
                                        (ID, {'namespace': ''}), (ID, {'namespace': 'a/b'}), (' ' + ID, {})])
def test_reference_rules(ref, kwargs):
    # A slug needs explicit namespace context; the namespace is given once.
    with pytest.raises(ValueError):
        notes(ref, **kwargs)


def test_slug_resolves_within_its_namespace_then_reads_by_id():
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.path == f'/namespaces/{NS}/notes':
            return httpx.Response(200, json={'notes': [{'id': ID, 'slug': 'design-doc', 'name': 'Design'}], 'total': 1}, request=req)
        return httpx.Response(200, json=snap('s').to_dict(), request=req)
    for handle in (client(handler).notes('design-doc', namespace=NS), client(handler).notes(f'{NS}/design-doc')):
        assert handle.read().note == ID and handle.id == ID
    assert [r.url.path for r in calls] == [f'/namespaces/{NS}/notes', f'/namespaces/{NS}/notes/{ID}/body'] * 2


@pytest.mark.parametrize('ref', [ID + '\n', f'{NS}/{ID}\n', '\n' + ID, ID + ' '])
def test_reference_with_surrounding_whitespace_is_refused_locally(ref):
    # Same `$`-before-newline trap as command ids: refused before any request.
    with pytest.raises(ValueError):
        client(never).notes(ref)


# ── read (source) ────────────────────────────────────────────────────────────

def test_read_is_the_v2_source_read():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=snap('Hello world.\n').to_dict(), request=req)
    got = client(handler).notes(ID, namespace=NS).read()
    assert isinstance(got, NoteSnapshot) and got == snap('Hello world.\n')
    (req,) = calls
    assert req.method == 'GET' and req.url.path == f'/namespaces/{NS}/notes/{ID}/body'
    assert dict(req.url.params) == {'contract': 'v2'} and 'if-match' not in req.headers
    assert client(handler).notes(ID, namespace=NS).read(view='source') == got


@pytest.mark.parametrize('at', [REV, snap('', REV), {'revision': REV, 'note': ID, 'content': '', 'hash': 'x'}])
def test_read_at_retained_revision(at):
    def handler(req):
        assert dict(req.url.params) == {'contract': 'v2', 'at': REV}
        return httpx.Response(200, json=snap('old').to_dict(), request=req)
    assert client(handler).notes(ID, namespace=NS).read(at=at).revision == REV


@pytest.mark.parametrize('bad', ['sha256:' + 'a' * 64, 'a' * 64, '"' + 'a' * 64 + '"'])
def test_read_refuses_hash_where_revision_expected(bad):
    with pytest.raises(RevisionExpected) as error:
        client(never).notes(ID, namespace=NS).read(at=bad)
    assert error.value.outcome == 'invalid-usage'


@pytest.mark.parametrize('bad', ['', 'rtc:', 'original:4', 'rtc:a\nb', 7])
def test_read_refuses_non_revision_strings(bad):
    with pytest.raises(ValueError):
        client(never).notes(ID, namespace=NS).read(at=bad)


def test_read_selector_rules():
    handle = client(never).notes(ID, namespace=NS)
    with pytest.raises(ValueError):
        handle.read(view='html')
    with pytest.raises(ValueError, match="view='mapped'"):
        handle.read(section='s1')
    with pytest.raises(ValueError, match='choose one'):
        handle.read(view='mapped', section='s1', address='s1.p2')
    with pytest.raises(TypeError):
        handle.read('mapped')


@pytest.mark.parametrize('bad', [
    snap('x', revision='sha256:' + 'a' * 64),
    snap('x', revision='sha256:' + 'a' * 64).to_dict(),
    snap('x', revision=snap('x').hash),
])
def test_snapshot_carrying_a_hash_as_revision_is_refused_everywhere(bad):
    handle = client(never).notes(ID, namespace=NS)
    with pytest.raises(RevisionExpected):
        handle.read(at=bad)
    with pytest.raises(RevisionExpected):
        handle.append(text='b', at=bad)
    with pytest.raises(RevisionExpected):
        handle.write(text='b', at=bad, mode='exact')
    with pytest.raises(RevisionExpected):
        handle.patch(patch='@@ chars 0:1 @@\n~ [-x-]\n', format='inline-dff', at=bad, mode='exact')
    with pytest.raises(RevisionExpected):
        handle.replace(text='y', lines=(1, 1), at=bad)


def test_snapshot_with_a_non_rtc_revision_is_refused():
    bad = snap('x', revision='original:4')
    handle = client(never).notes(ID, namespace=NS)
    with pytest.raises(ValueError, match='rtc:'):
        handle.append(text='b', at=bad.to_dict())
    with pytest.raises(ValueError, match='rtc:'):
        handle.read(at=bad)


def test_read_results_of_another_note_are_refused_before_io():
    other = snap('x', note=OTHER)
    focused = FocusedRead(note=OTHER, hash=other.hash, revision=REV, scope={'lines': [1, 1]}, lines=(1, 1), chars=(0, 1), content='x')
    handle = client(never).notes(ID, namespace=NS)
    for at in (other, other.to_dict(), focused):
        with pytest.raises(ValueError, match='belongs to note'):
            handle.patch(patch='@@ chars 0:1 @@\n~ [-x-]\n', format='inline-dff', at=at, mode='exact')
        with pytest.raises(ValueError, match='belongs to note'):
            handle.read(at=at)
        with pytest.raises(ValueError, match='belongs to note'):
            handle.write(text='y', at=at, mode='exact')
        with pytest.raises(ValueError, match='belongs to note'):
            handle.append(text='y', at=at)
        with pytest.raises(ValueError, match='belongs to note'):
            handle.remove(lines=(1, 1), at=at)


# ── append ───────────────────────────────────────────────────────────────────

def test_append_defaults_to_merge_and_sends_one_record_at_the_document_end():
    calls = []
    def handler(req):
        calls.append(req)
        return ack(req)
    receipt = client(handler).notes(ID, namespace=NS).append(text='Hi', at=snap('Hello world.\n'))
    assert isinstance(receipt, PatchReceipt) and receipt.mode == 'merge' and receipt.revision == NEXT
    (req,) = calls
    assert req.method == 'PATCH' and req.url.path == f'/namespaces/{NS}/notes/{ID}/body'
    assert json.loads(req.content) == {'format': 'inline-dff', 'patch': '@@ chars 13:13 @@\n~ {+Hi+}\n',
                                       'baseRevision': REV, 'mode': 'merge'}


def test_append_is_verbatim_with_no_implicit_separator():
    # The docs' rule: the payload carries every intended newline. "Hello world."
    # + "Hi" is "Hello world.Hi"; a source ending in LF plus "\nHello\n" keeps a blank line.
    sent = []
    def handler(req):
        sent.append(json.loads(req.content)['patch'])
        return ack(req)
    handle = client(handler).notes(ID, namespace=NS)
    handle.append(text='Hi', at=snap('Hello world.'))
    handle.append(text='\nHello\n', at=snap('Hello world.\n'))
    handle.append(text='Hi', at=snap(''))
    assert sent == ['@@ chars 12:12 @@\n~ {+Hi+}\n', '@@ chars 13:13 @@\n~ {+\\nHello\\n+}\n', '@@ chars 0:0 @@\n~ {+Hi+}\n']


def test_append_exact_and_invalid_modes():
    def handler(req):
        assert json.loads(req.content)['mode'] == 'exact'
        return ack(req)
    assert client(handler).notes(ID, namespace=NS).append(text='Hi', at=snap('x\n'), mode='exact').mode == 'exact'
    for bad in (None, 'fuzzy', True, 'EXACT'):
        with pytest.raises(ValueError):
            client(never).notes(ID, namespace=NS).append(text='Hi', at=snap(''), mode=bad)


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
    client(handler).notes(ID, namespace=NS).append(text='Hi', at=REV, mode='exact')
    assert [r.method for r in calls] == ['GET', 'PATCH']


def test_append_accepts_a_snapshot_mapping_and_a_complete_mapped_read():
    original = snap('a\n')
    calls = []
    def handler(req):
        calls.append(json.loads(req.content))
        return ack(req)
    handle = client(handler).notes(ID, namespace=NS)
    handle.append(text='b', at=original.to_dict())
    mapped = MappedRead(note=ID, hash=original.hash, revision=REV, scope={'article': True}, chars=(0, 2), content='a\n',
                        source_type='markdown', elements=(), complete=True)
    handle.append(text='b', at=mapped)
    assert [c['baseRevision'] for c in calls] == [REV, REV] and all(c['patch'].startswith('@@ chars 2:2 @@') for c in calls)


def test_append_refuses_partial_reads_and_tampered_snapshots():
    handle = client(never).notes(ID, namespace=NS)
    focused = FocusedRead(note=ID, hash=snap('a\nb\n').hash, revision=REV, scope={'lines': [1, 1]}, lines=(1, 1), chars=(0, 2), content='a\n')
    with pytest.raises(SnapshotRequired):
        handle.append(text='c', at=focused)
    partial = MappedRead(note=ID, hash=snap('a\nb\n').hash, revision=REV, scope={'section': 's1'}, chars=(0, 2), content='a\n',
                         source_type='markdown', elements=(), complete=False)
    with pytest.raises(SnapshotRequired):
        handle.append(text='c', at=partial)
    with pytest.raises(ValueError, match='content hash'):
        handle.append(text='c', at=NoteSnapshot(ID, 'tampered', snap('a').hash, REV))


def test_empty_append_is_a_local_no_write_unless_exact_or_identified():
    assert client(never).notes(ID, namespace=NS).append(text='', at=snap('a\n')).outcome == 'no-write'
    sent = []
    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json=dict(note=ID, mode=sent[-1]['mode'], baseRevision=REV, hash=snap('a\n').hash,
                                             revision=REV, outcome='no-write', commandId=sent[-1].get('commandId'),
                                             replayed=False), request=req)
    client(handler).notes(ID, namespace=NS).append(text='', at=snap('a\n'), mode='exact')
    client(handler).notes(ID, namespace=NS).append(text='', at=snap('a\n'), command_id='cmd-conformance-0001')
    assert [c['patch'] for c in sent] == ['', ''] and sent[1]['commandId'] == 'cmd-conformance-0001'


@pytest.mark.parametrize('method,payload', [('append', {'text': 'x'}), ('write', {'text': 'x', 'mode': 'exact'}),
                                            ('patch', {'patch': '@@ chars 0:0 @@\n~ {+x+}\n', 'format': 'inline-dff', 'mode': 'exact'}),
                                            ('replace', {'text': 'x', 'lines': (1, 1)}), ('insert', {'text': 'x', 'cursor': 0})])
def test_writes_refuse_reading_view_marker_before_any_request(method, payload):
    handle = client(never).notes(ID, namespace=NS)
    key = 'patch' if method == 'patch' else 'text'
    payload = dict(payload, **{key: 'ok\n<!-- notes-reading-view page=1 -->\n'})
    with pytest.raises(ReadingViewRefused) as error:
        getattr(handle, method)(**payload, at=snap('x\n'))
    assert error.value.outcome == 'invalid-usage'


# ── keyword-only contract ────────────────────────────────────────────────────

@pytest.mark.parametrize('method', ['read', 'outline', 'find', 'diff', 'replace', 'insert', 'remove', 'append', 'write',
                                    'patch', 'command_status', 'rename', 'visit'])
def test_every_handle_operation_takes_keyword_arguments_only(method):
    params = inspect.signature(getattr(NoteHandle, method)).parameters
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for name, p in params.items() if name != 'self'), method
    handle = client(never).notes(ID, namespace=NS)
    with pytest.raises(TypeError):
        getattr(handle, method)('positional')


def test_write_and_patch_require_mode_and_patch_requires_format():
    handle = client(never).notes(ID, namespace=NS)
    with pytest.raises(TypeError):
        handle.write(text='x', at=snap(''))
    with pytest.raises(TypeError):
        handle.patch(patch='', at=snap(''), mode='exact')
    with pytest.raises(TypeError):
        handle.patch(patch='', format='diff', at=snap(''))
    with pytest.raises(ValueError):
        handle.patch(patch='', format='unified', at=snap(''), mode='exact')


# ── write / patch delegation ─────────────────────────────────────────────────

def test_write_delegates_disjoint_plan_against_snapshot():
    original = snap('Draft middle Draft')
    def handler(req):
        data = json.loads(req.content)
        assert req.method == 'PATCH' and data['mode'] == 'merge' and data['baseRevision'] == REV
        assert ' middle ' not in data['patch']
        return ack(req)
    assert client(handler).notes(ID, namespace=NS).write(text='Done middle Done', at=original, mode='merge').revision == NEXT


def test_patch_sends_the_documented_unified_diff_example():
    original = snap('Hello world.\n')
    patch = '--- before.md\n+++ after.md\n@@ -1 +1 @@\n-Hello world.\n+Hello team.\n'
    def handler(req):
        assert json.loads(req.content) == {'format': 'diff', 'patch': patch, 'baseRevision': REV, 'mode': 'exact'}
        return ack(req)
    handle = client(handler).notes(ID, namespace=NS)
    assert handle.patch(patch=patch, format='diff', at=original, mode='exact').mode == 'exact'
    assert handle.patch(patch=patch, format='diff', at=REV, mode='exact').base_revision == REV


# ── collection: list / create ────────────────────────────────────────────────

def test_list_requires_namespace_and_lists_it():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={'notes': [{'id': ID, 'slug': 'a', 'name': 'A'}], 'total': 1}, request=req)
    got = client(handler).notes.list(namespace=NS, limit=20, offset=40)
    assert got == [NoteRef(id=ID, namespace=NS, name='A', slug='a')]
    (req,) = calls
    assert req.method == 'GET' and req.url.path == f'/namespaces/{NS}/notes'
    assert dict(req.url.params) == {'limit': '20', 'offset': '40', 'includeArchived': 'false'}
    with pytest.raises(TypeError):
        client(never).notes.list()
    with pytest.raises(TypeError):
        client(never).notes.list(NS)


def test_create_empty_note_is_one_request_and_returns_a_handle():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(201, json={'note': {'id': ID, 'name': 'Untitled'}}, request=req)
    handle = client(handler).notes.create(namespace=NS)
    assert isinstance(handle, NoteHandle) and handle.id == ID and handle.namespace == NS
    (req,) = calls
    assert req.method == 'POST' and req.url.path == f'/namespaces/{NS}/notes'
    assert json.loads(req.content) == {'name': 'Untitled', 'visibility': 'private'}


def test_create_with_source_initializes_through_the_retained_baseline_contract():
    calls = []
    def handler(req):
        calls.append(req)
        if req.method == 'POST':
            assert json.loads(req.content) == {'name': 'Review', 'visibility': 'private'}
            return httpx.Response(201, json={'note': {'id': ID}}, request=req)
        if req.method == 'GET':
            return httpx.Response(200, json=snap('').to_dict(), request=req)
        data = json.loads(req.content)
        assert data == {'format': 'inline-dff', 'patch': '@@ chars 0:0 @@\n~ {+# Review\\n+}\n', 'baseRevision': REV, 'mode': 'exact'}
        return ack(req)
    handle = client(handler).notes.create(namespace=NS, name='Review', text='# Review\n')
    assert handle.id == ID
    assert [(r.method, r.url.path) for r in calls] == [('POST', f'/namespaces/{NS}/notes'),
                                                        ('GET', f'/namespaces/{NS}/notes/{ID}/body'),
                                                        ('PATCH', f'/namespaces/{NS}/notes/{ID}/body')]


def test_create_source_failure_exposes_the_created_id_instead_of_a_second_note():
    def handler(req):
        if req.method == 'POST':
            return httpx.Response(201, json={'note': {'id': ID}}, request=req)
        return httpx.Response(503, json={'error': 'rtc_unavailable'}, request=req)
    with pytest.raises(NoteCreatedWithoutSource) as error:
        client(handler).notes.create(namespace=NS, text='# Review\n')
    assert error.value.note_id == ID and error.value.namespace == NS and error.value.outcome == 'unknown'


def test_create_argument_rules():
    with pytest.raises(TypeError):
        client(never).notes.create(NS)
    with pytest.raises(ValueError):
        client(never).notes.create(namespace='')
    with pytest.raises(ValueError):
        client(never).notes.create(namespace=NS, name='  ')
    with pytest.raises(ValueError):
        client(never).notes.create(namespace=NS, visibility='team')
    with pytest.raises(ReadingViewRefused):
        client(never).notes.create(namespace=NS, text='<!-- notes-reading-view -->')


# ── outcome categories ───────────────────────────────────────────────────────

def test_errors_carry_the_documented_outcome_categories():
    assert NoteChanged('x').outcome == 'no-write' and NoteNotFound('x').outcome == 'no-write'
    assert CommandIntentMismatchError('cmd-conformance-0001').outcome == 'no-write'
    assert UncertainOutcomeError('cmd-conformance-0001').outcome == 'unknown' and WriteOutcomeUnknown('x').outcome == 'unknown'
    assert NoteBusy('x').outcome == 'operational' and NoteError('x').outcome == 'operational'
    assert RevisionExpected('x').outcome == SnapshotRequired('x').outcome == 'invalid-usage'
    def handler(req):
        raise httpx.ReadError('lost', request=req)
    with pytest.raises(WriteOutcomeUnknown, match='write outcome unknown'):
        Note(ID, namespace=NS, client=client(handler)).append('x', at=snap('a'))
