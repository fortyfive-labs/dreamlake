"""Command identity on v2 body mutations (Phase 1 contract A), client side.

Opt-in: no `command_id` means nothing new on the wire. Supplied, it is sent as
`commandId`, the receipt must echo it, and a failure after the request left is
a typed uncertain outcome that carries the id. Replay is the server's job; the
client never resends on its own.
"""
import hashlib
import json

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api.notes import (
    CommandIdentityUnsupportedError,
    CommandInProgressError,
    CommandIntentMismatchError,
    CommandStatus,
    Note,
    NoteBusy,
    NoteError,
    NoteNotFound,
    NoteSnapshot,
    PatchReceipt,
    UncertainOutcomeError,
)

ID = '6ae3f318608512b4ba34860c'
REV = 'rtc:' + 'c9' * 32
NEXT = 'rtc:' + 'ab' * 32
CMD = 'cmd-conformance-0001'
PATCH = '@@ chars 0:5 @@\n~ [-Hello-]{+Welcome+}\n'


def snap(text, revision=REV):
    return NoteSnapshot(ID, text, 'sha256:' + hashlib.sha256(text.encode()).hexdigest(), revision)


def make(handler):
    return Note(ID, namespace='ns', client=DreamLakeClient(dl_url='https://example.test', token='test',
                                                          transport=httpx.MockTransport(handler)))


def receipt_for(req, **extra):
    data = json.loads(req.content)
    body = dict(note=ID, mode=data['mode'], baseRevision=data['baseRevision'], hash=snap('x').hash, revision=NEXT)
    body.update(extra)
    return httpx.Response(200, json=body, request=req)


# ── validation, before any request ───────────────────────────────────────────

@pytest.mark.parametrize('bad', ['short7!', 'a' * 7, 'a' * 129, 'has space', 'tab\tbed', 'ünïcode-id', 42, ''])
def test_command_id_charset_and_length_are_checked_before_io(bad):
    note = Note(ID, namespace='ns', client=object())
    for call in (lambda: note.patch(PATCH, base_revision=REV, command_id=bad),
                 lambda: note.append('x', at=snap(''), command_id=bad),
                 lambda: note.write('x', at=snap(''), command_id=bad),
                 lambda: note.command_status(bad)):
        with pytest.raises(ValueError):
            call()


@pytest.mark.parametrize('good', ['a' * 8, 'a' * 128, 'cmd.2026-10-30:run_1', CMD])
def test_command_id_accepted_charset(good):
    def handler(req):
        assert json.loads(req.content)['commandId'] == good
        return receipt_for(req, outcome='accepted', commandId=good, replayed=False)
    assert make(handler).patch(PATCH, base_revision=REV, command_id=good).command_id == good


def test_legacy_paths_have_no_command_identity():
    note = Note(ID, namespace='ns', client=object())
    with pytest.raises(ValueError):
        note.patch(PATCH, legacy=True, command_id=CMD)
    with pytest.raises(ValueError):
        note.append('x', legacy=True, command_id=CMD)
    with pytest.raises(ValueError):
        note.write('x', force=True, command_id=CMD)


# ── wire ─────────────────────────────────────────────────────────────────────

def test_absent_command_id_sends_nothing_new_and_receipt_fields_are_none():
    def handler(req):
        assert json.loads(req.content) == {'format': 'inline-dff', 'patch': PATCH, 'baseRevision': REV, 'mode': 'merge'}
        return receipt_for(req)
    receipt = make(handler).patch(PATCH, base_revision=REV)
    assert (receipt.outcome, receipt.command_id, receipt.replayed) == (None, None, None)
    assert set(receipt.to_dict()) == {'note', 'mode', 'baseRevision', 'hash', 'revision'}


def test_absent_command_id_still_reads_outcome_from_a_newer_server():
    receipt = make(lambda req: receipt_for(req, outcome='accepted', commandId=None, replayed=False)).patch(
        PATCH, base_revision=REV)
    assert receipt.outcome == 'accepted' and receipt.command_id is None and receipt.replayed is False
    assert receipt.to_dict()['outcome'] == 'accepted' and 'commandId' not in receipt.to_dict()


def test_supplied_command_id_is_sent_echoed_and_surfaced():
    def handler(req):
        body = json.loads(req.content)
        assert body == {'format': 'inline-dff', 'patch': PATCH, 'baseRevision': REV, 'mode': 'exact', 'commandId': CMD}
        return receipt_for(req, outcome='accepted', commandId=CMD, replayed=False)
    receipt = make(handler).patch(PATCH, base_revision=REV, exact=True, command_id=CMD)
    assert isinstance(receipt, PatchReceipt)
    assert (receipt.outcome, receipt.command_id, receipt.replayed) == ('accepted', CMD, False)
    assert receipt.to_dict() == dict(note=ID, mode='exact', baseRevision=REV, hash=snap('x').hash, revision=NEXT,
                                     outcome='accepted', commandId=CMD, replayed=False)


def test_replayed_receipt_is_returned_as_the_server_stored_it():
    receipt = make(lambda req: receipt_for(req, outcome='no-write', commandId=CMD, replayed=True)).append(
        'tail', at=snap('x'), command_id=CMD)
    assert receipt.replayed is True and receipt.outcome == 'no-write'


def test_receipt_without_echo_is_typed_unsupported_and_cache_untouched():
    note = make(lambda req: receipt_for(req))  # an older server: no commandId in the receipt
    note._text, note._etag = 'cache', 'legacy'
    with pytest.raises(CommandIdentityUnsupportedError, match=CMD):
        note.patch(PATCH, base_revision=REV, command_id=CMD)
    assert (note._text, note._etag) == ('cache', 'legacy')


@pytest.mark.parametrize('change', [{'commandId': 'cmd-somebody-else-01'}, {'outcome': 'maybe'}, {'replayed': 'yes'}])
def test_inconsistent_identity_fields_are_uncertain_not_success(change):
    note = make(lambda req: receipt_for(req, **dict({'outcome': 'accepted', 'commandId': CMD, 'replayed': False}, **change)))
    with pytest.raises(UncertainOutcomeError) as error:
        note.patch(PATCH, base_revision=REV, command_id=CMD)
    assert error.value.command_id == CMD


def test_unsolicited_command_id_in_receipt_is_inconsistent():
    with pytest.raises(NoteError, match='write outcome unknown'):
        make(lambda req: receipt_for(req, commandId=CMD)).patch(PATCH, base_revision=REV)


# ── uncertainty ──────────────────────────────────────────────────────────────

def test_transport_failure_after_send_with_command_id_is_uncertain_outcome():
    calls = []
    def handler(req):
        calls.append(req)
        raise httpx.ReadError('lost ack', request=req)
    with pytest.raises(UncertainOutcomeError) as error:
        make(handler).append('tail', at=snap('x'), command_id=CMD)
    assert error.value.command_id == CMD
    assert 'same command_id' in str(error.value) and 'command_status' in str(error.value)
    assert len(calls) == 1  # never replayed by the client


def test_transport_failure_without_command_id_keeps_the_untyped_unknown():
    def handler(req):
        raise httpx.ReadError('lost ack', request=req)
    with pytest.raises(NoteError, match='write outcome unknown') as error:
        make(handler).append('tail', at=snap('x'))
    assert not isinstance(error.value, UncertainOutcomeError)


@pytest.mark.parametrize('failure', [httpx.ConnectError, httpx.ConnectTimeout, httpx.WriteError, httpx.ReadError,
                                     httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.PoolTimeout])
def test_any_transport_failure_after_handoff_with_command_id_is_uncertain(failure):
    # Regression: a transport can forward the PATCH and then raise ConnectError,
    # so "not sent" was never a safe classification for anything it raises.
    forwarded = []
    def handler(req):
        forwarded.append(json.loads(req.content))
        raise failure('after forwarding', request=req)
    with pytest.raises(UncertainOutcomeError) as error:
        make(handler).append('tail', at=snap('x'), command_id=CMD)
    assert error.value.command_id == CMD and 'command_status' in str(error.value)
    assert forwarded == [{'format': 'inline-dff', 'patch': '@@ chars 1:1 @@\n~ {+\\ntail+}\n',
                          'baseRevision': REV, 'mode': 'merge', 'commandId': CMD}]


@pytest.mark.parametrize('status', [500, 502, 204])
def test_unknown_status_with_command_id_is_uncertain(status):
    with pytest.raises(UncertainOutcomeError) as error:
        make(lambda req: httpx.Response(status, json={}, request=req)).patch(PATCH, base_revision=REV, command_id=CMD)
    assert error.value.command_id == CMD


@pytest.mark.parametrize('code,error', [('command_outcome_unknown', UncertainOutcomeError),
                                        ('command_in_progress', CommandInProgressError),
                                        ('command_intent_mismatch', CommandIntentMismatchError)])
def test_409_about_the_command_id_is_typed_not_busy(code, error):
    # Regression: these went through _raise_for() as NoteBusy, which reads as
    # "the room is unreachable" and invites a fresh write under a new id.
    def handler(req):
        return httpx.Response(409, json={'error': code, 'commandId': CMD, 'message': 'see status'}, request=req)
    with pytest.raises(error) as raised:
        make(handler).patch(PATCH, base_revision=REV, command_id=CMD)
    assert raised.value.command_id == CMD and not isinstance(raised.value, NoteBusy)
    if error is UncertainOutcomeError:
        assert 'command_status' in str(raised.value)


def test_other_409s_and_409s_without_a_command_id_stay_busy():
    def overlap(req):
        return httpx.Response(409, json={'error': 'overlap', 'message': 'concurrent'}, request=req)
    with pytest.raises(NoteBusy):
        make(overlap).patch(PATCH, base_revision=REV, command_id=CMD)
    def unknown(req):
        return httpx.Response(409, json={'error': 'command_outcome_unknown'}, request=req)
    with pytest.raises(NoteBusy):
        make(unknown).patch(PATCH, base_revision=REV)


def test_deterministic_rejections_stay_typed_even_with_command_id():
    def handler(req):
        return httpx.Response(412, json={'error': 'stale', 'message': 'changed'}, request=req)
    from dreamlake.api.notes import NoteChanged
    with pytest.raises(NoteChanged):
        make(handler).patch(PATCH, base_revision=REV, exact=True, command_id=CMD)


# ── no-write paths ───────────────────────────────────────────────────────────

def test_unchanged_merge_without_command_id_is_a_local_no_write_receipt():
    receipt = make(lambda req: pytest.fail('no request expected')).append('', at=snap('x\n'))
    assert receipt.outcome == 'no-write' and receipt.command_id is None and receipt.replayed is None
    assert receipt.revision == REV
    receipt = make(lambda req: pytest.fail('no request expected')).write('same', at=snap('same'))
    assert receipt.outcome == 'no-write'


def test_unchanged_merge_with_command_id_is_submitted_so_the_server_records_it():
    calls = []
    def handler(req):
        calls.append(json.loads(req.content))
        return receipt_for(req, outcome='no-write', commandId=CMD, replayed=False)
    note = make(handler)
    assert note.append('', at=snap('x\n'), command_id=CMD).outcome == 'no-write'
    assert note.write('same', at=snap('same'), command_id=CMD).outcome == 'no-write'
    assert [c['patch'] for c in calls] == ['', ''] and all(c['commandId'] == CMD for c in calls)


# ── status ───────────────────────────────────────────────────────────────────

def test_command_status_reads_the_command_endpoint():
    stored = dict(note=ID, mode='exact', baseRevision=REV, hash=snap('x').hash, revision=NEXT,
                  outcome='accepted', commandId=CMD, replayed=False)
    def handler(req):
        assert req.method == 'GET' and req.url.path == f'/namespaces/ns/notes/{ID}/commands/{CMD}'
        return httpx.Response(200, json={'commandId': CMD, 'state': 'accepted', 'outcome': 'accepted',
                                         'receipt': stored, 'createdAt': 't0', 'updatedAt': 't1'}, request=req)
    status = make(handler).command_status(CMD)
    assert isinstance(status, CommandStatus)
    assert (status.state, status.outcome, status.created_at, status.updated_at) == ('accepted', 'accepted', 't0', 't1')
    assert status.receipt == PatchReceipt(ID, 'exact', REV, snap('x').hash, NEXT, 'accepted', CMD, False)


def test_command_status_unknown_id_and_pending_state():
    with pytest.raises(NoteNotFound):
        make(lambda req: httpx.Response(404, json={'error': 'command_not_found'}, request=req)).command_status(CMD)
    status = make(lambda req: httpx.Response(200, json={'commandId': CMD, 'state': 'pending'}, request=req)).command_status(CMD)
    assert status.state == 'pending' and status.receipt is None and status.outcome is None


def test_command_status_rejects_a_mismatched_or_invalid_envelope():
    with pytest.raises(NoteError):
        make(lambda req: httpx.Response(200, json={'commandId': 'other-command-id', 'state': 'accepted'}, request=req)).command_status(CMD)
    with pytest.raises(NoteError):
        make(lambda req: httpx.Response(200, text='not json', request=req)).command_status(CMD)


# ── facade pass-through ──────────────────────────────────────────────────────

def test_facade_passes_command_id_and_exposes_status():
    calls = []
    def handler(req):
        calls.append(req)
        if req.method == 'GET':
            return httpx.Response(200, json={'commandId': CMD, 'state': 'accepted'}, request=req)
        assert json.loads(req.content)['commandId'] == CMD
        return receipt_for(req, outcome='accepted', commandId=CMD, replayed=False)
    client = DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(handler))
    handle = client.notes(f'ns/{ID}')
    assert handle.append(text='Hi', at=snap('x\n'), mode='exact', command_id=CMD).command_id == CMD
    assert handle.write('y', at=snap('x'), mode='merge', command_id=CMD).command_id == CMD
    assert handle.patch(PATCH, at=REV, mode='merge', command_id=CMD).command_id == CMD
    assert handle.command_status(CMD).state == 'accepted'
    assert [r.method for r in calls] == ['PATCH', 'PATCH', 'PATCH', 'GET']
