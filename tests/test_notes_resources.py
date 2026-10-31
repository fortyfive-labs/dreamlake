"""Resource and collaboration operations on the handle: rename, versions, files.move, presence, visit.

Each mirrors a request the CLI already makes (`notes rename`, `note hist`,
`notes files mv`, `notes presence`, `notes visit`); the facade adds keyword
fields and verification, not new protocol.
"""
import hashlib
import json

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api._notes_facade import AgentIdentityRequired, MappedRead, Roster, SavedVersion, VersionPage, VisitReceipt
from dreamlake.api.notes import NoteError, NoteRef, NoteSnapshot


@pytest.fixture(autouse=True)
def agent_identity(monkeypatch):
    # visit is attributed; the CLI's `notes visit` reads the same variables.
    monkeypatch.setenv('DREAMLAKE_AGENT_ID', 'review-session-42')
    monkeypatch.delenv('DREAMLAKE_AGENT_NAME', raising=False)

NS = 'fortyfive'
ID = '6ae3f318608512b4ba34860c'
REV = 'rtc:' + 'c9' * 32
FILE = '6ae3f318608512b4ba34860d'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def snap(text):
    return NoteSnapshot(ID, text, 'sha256:' + sha(text), REV)


def handle(handler):
    return DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(handler)).notes(ID, namespace=NS)


def never(req):
    raise AssertionError(f'unexpected request {req.method} {req.url}')


# ── rename ───────────────────────────────────────────────────────────────────

def test_rename_is_a_metadata_patch_without_a_body_revision():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={'id': ID, 'name': 'New title', 'slug': 'new-title', 'namespaceSlug': NS}, request=req)
    got = handle(handler).rename(name='New title')
    assert got == NoteRef(id=ID, namespace=NS, name='New title', slug='new-title')
    (req,) = calls
    assert req.method == 'PATCH' and req.url.path == f'/namespaces/{NS}/notes/{ID}'
    assert json.loads(req.content) == {'name': 'New title'} and 'if-match' not in req.headers
    with pytest.raises(ValueError):
        handle(never).rename(name=' ')


# ── versions ─────────────────────────────────────────────────────────────────

def test_versions_list_and_read_verify_identity_and_hash():
    text = '# Saved\n'
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.path.endswith('/versions'):
            return httpx.Response(200, json={'versions': [{'id': 'v_1_a', 'tag': 'release', 'createdAt': 't', 'summary': 's', 'hash': sha(text)}],
                                             'nextCursor': 'v_0_z'}, request=req)
        return httpx.Response(200, json={'id': 'v_1_a', 'tag': 'release', 'createdAt': 't', 'summary': 's', 'hash': sha(text), 'text': text}, request=req)
    note = handle(handler)
    page = note.versions.list()
    assert page == VersionPage(versions=(SavedVersion('v_1_a', 'release', 't', 's', sha(text)),), next_cursor='v_0_z')
    note.versions.list(before='v_0_z')
    saved = note.versions.read(version_id='v_1_a')
    assert saved.text == text and saved.id == 'v_1_a'
    assert [(r.method, r.url.path, dict(r.url.params)) for r in calls] == [
        ('GET', f'/namespaces/{NS}/notes/{ID}/versions', {}), ('GET', f'/namespaces/{NS}/notes/{ID}/versions', {'before': 'v_0_z'}),
        ('GET', f'/namespaces/{NS}/notes/{ID}/versions/v_1_a', {})]


def test_version_read_refuses_a_mismatched_record():
    def wrong_hash(req):
        return httpx.Response(200, json={'id': 'v_1_a', 'hash': sha('other'), 'text': '# Saved\n'}, request=req)
    with pytest.raises(NoteError, match='hash'):
        handle(wrong_hash).versions.read(version_id='v_1_a')
    def wrong_id(req):
        return httpx.Response(200, json={'id': 'v_2_b', 'hash': sha('x'), 'text': 'x'}, request=req)
    with pytest.raises(NoteError, match='identity'):
        handle(wrong_id).versions.read(version_id='v_1_a')
    with pytest.raises(ValueError):
        handle(never).versions.read(version_id='not-a-version')
    with pytest.raises(TypeError):
        handle(never).versions.read('v_1_a')


# ── files.move ───────────────────────────────────────────────────────────────

def test_files_move_computes_the_destination_path_from_folder_and_name():
    calls = []
    def handler(req):
        calls.append(req)
        row = {'id': FILE, 'path': 'old/a.md', 'contentType': 'text/markdown', 'sizeBytes': 1, 'etag': 'e'}
        if req.method == 'PATCH':
            row = dict(row, path=json.loads(req.content)['path'])
        return httpx.Response(200, json=row, request=req)
    note = handle(handler)
    assert note.files.move(file=FILE, to_folder='reviews/2026', name='final.md').path == 'reviews/2026/final.md'
    assert note.files.move(file=FILE, name='final.md').path == 'old/final.md'
    assert note.files.move(file=FILE, to_folder='/reviews/').path == 'reviews/a.md'
    patches = [json.loads(r.content) for r in calls if r.method == 'PATCH']
    assert patches == [{'path': 'reviews/2026/final.md', 'overwrite': False}, {'path': 'old/final.md', 'overwrite': False},
                       {'path': 'reviews/a.md', 'overwrite': False}]
    assert all(r.url.path.endswith(f'/files/{FILE}') for r in calls)
    with pytest.raises(ValueError):
        note.files.move(file=FILE)
    with pytest.raises(ValueError):
        note.files.move(file=FILE, name='a/b')


# ── presence / visit ─────────────────────────────────────────────────────────

def test_presence_reads_the_roster_without_joining():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={'participants': [{'user': {'id': 'u1'}, 'expiresAt': 1}], 'observedAt': 42}, request=req)
    got = handle(handler).presence()
    assert got == Roster(note=ID, participants=({'user': {'id': 'u1'}, 'expiresAt': 1},), observed_at=42)
    (req,) = calls
    assert req.method == 'GET' and req.url.path == f'/namespaces/{NS}/notes/{ID}/presence' and dict(req.url.params) == {'format': 'json'}


def test_visit_joins_once_with_an_optional_intent():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={'state': {'user': {'id': 'u1'}, 'expiresAt': 99}}, request=req)
    got = handle(handler).visit(intent='Reviewing this paragraph')
    assert isinstance(got, VisitReceipt) and got.expires_at == 99 and got.location is None
    (req,) = calls
    assert req.method == 'POST' and req.url.path == f'/namespaces/{NS}/notes/{ID}/presence'
    assert json.loads(req.content) == {'action': 'join', 'intent': {'summary': 'Reviewing this paragraph'}}
    handle(handler).visit()
    assert json.loads(calls[-1].content) == {'action': 'join'}


def test_located_visit_seeks_the_range_at_the_baseline_hash():
    source = 'First para.\n'
    original = snap(source)
    mapped = MappedRead(note=ID, hash=original.hash, revision=REV, scope={'article': True}, chars=(0, 12), content=source,
                        source_type='markdown', elements=(), complete=True)
    calls = []
    def handler(req):
        calls.append(json.loads(req.content))
        body = calls[-1]
        state = {'expiresAt': 7}
        if body['action'] == 'seek':
            state['activity'] = {'kind': 'seeking', 'hash': body['hash'], 'ranges': body['ranges']}
        return httpx.Response(200, json={'state': state}, request=req)
    got = handle(handler).visit(cursor=5, at=original)
    assert got.location == {'hash': original.hash, 'chars': [5, 5]} and got.expires_at == 7
    assert calls == [{'action': 'join'}, {'action': 'seek', 'hash': original.hash, 'ranges': [{'start': 5, 'end': 5}]}]
    from dreamlake.api._notes_facade import MappedElement
    addressed = MappedRead(note=ID, hash=original.hash, revision=REV, scope={'article': True}, chars=(0, 12), content=source,
                           source_type='markdown', elements=(MappedElement('s1.p1', (0, 11), (1, 1)),), complete=True)
    handle(handler).visit(intent='Here', address='s1.p1', at=addressed)
    assert calls[-1] == {'action': 'seek', 'hash': original.hash, 'ranges': [{'start': 0, 'end': 11}]}
    assert calls[-2] == {'action': 'join', 'intent': {'summary': 'Here'}}
    del mapped


def test_visit_rules_and_unacknowledged_location():
    note = handle(never)
    with pytest.raises(ValueError, match='1-280'):
        note.visit(intent='x' * 281)
    with pytest.raises(ValueError, match='1-280'):
        note.visit(intent='   ')
    with pytest.raises(ValueError, match='at most one'):
        note.visit(address='s1', cursor=1, at=snap('x'))
    with pytest.raises(ValueError, match='baseline'):
        note.visit(cursor=1)
    def handler(req):
        body = json.loads(req.content)
        state = {'expiresAt': 7, 'activity': {'hash': 'sha256:' + '0' * 64}} if body['action'] == 'seek' else {'expiresAt': 7}
        return httpx.Response(200, json={'state': state}, request=req)
    with pytest.raises(NoteError, match='acknowledge'):
        handle(handler).visit(cursor=0, at=snap('x'))


# ── visit attribution ────────────────────────────────────────────────────────

def test_visit_sends_the_agent_identity_headers_the_cli_sends(monkeypatch):
    # Regression: the attribution hook skipped /presence, so the server
    # answered 400 agent_identity_required.
    monkeypatch.setenv('DREAMLAKE_AGENT_NAME', 'Codex')
    seen = []
    def handler(req):
        seen.append(req)
        if req.method == 'GET':
            return httpx.Response(200, json={'participants': [], 'observedAt': 1}, request=req)
        return httpx.Response(200, json={'state': {'expiresAt': 1}}, request=req)
    handle(handler).visit(intent='Reviewing')
    (req,) = seen
    assert req.headers['x-dreamlake-agent-id'] == 'review-session-42' and req.headers['x-dreamlake-agent-name'] == 'Codex'
    handle(handler).presence()
    assert seen[-1].headers['x-dreamlake-agent-id'] == 'review-session-42'


def test_visit_without_an_agent_identity_is_refused_before_any_request(monkeypatch):
    monkeypatch.delenv('DREAMLAKE_AGENT_ID')
    with pytest.raises(AgentIdentityRequired) as error:
        handle(never).visit(intent='Reviewing')
    assert error.value.outcome == 'invalid-usage' and isinstance(error.value, ValueError)
    monkeypatch.setenv('DREAMLAKE_AGENT_ID', '')
    with pytest.raises(AgentIdentityRequired):
        handle(never).visit()
