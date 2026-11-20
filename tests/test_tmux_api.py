import json

import httpx
import pytest

from dreamlake.api import TmuxError, TmuxReference, parse_tmux_reference, tmux_directive
from dreamlake.api._client import DreamLakeClient

ENROLLMENT = 'a' * 24
HOST = 'c' * 24
REQUEST_ID = '11111111-2222-4333-8444-555555555555'
REF = TmuxReference(ENROLLMENT, 'b' * 16, "work / [literal]; $(no) 'quoted'")
TARGET = dict(namespace='test', enrollmentId=ENROLLMENT, machineId='machine', username='ubuntu')
RESOLVED = dict(version=1, multiplexer_references=1, status='resolved', reason='session_found', observedAt='2026-11-20T21:00:00Z', reference=REF.as_dict(), target=TARGET)
COMPLETE = dict(version=1, operation='create', requestId=REQUEST_ID, requestedName='work', status='succeeded', reference=REF.as_dict(), directive=REF.directive(), target=TARGET)


def client(handler, remote='https://api.example'):
    return DreamLakeClient(dl_url=remote, token='synthetic-test-token', transport=httpx.MockTransport(handler))


def test_reference_roundtrip_matches_javascript_and_rejects_ambiguous_scopes():
    assert tmux_directive(REF) == ":tmux[aaaaaaaaaaaaaaaaaaaaaaaa/bbbbbbbbbbbbbbbb/work%20%2F%20%5Bliteral%5D%3B%20%24(no)%20'quoted']"
    assert parse_tmux_reference(REF.directive()) == REF
    assert parse_tmux_reference(REF.value()) == REF
    for text in [':tmux[test/machine/user/work]', f'{ENROLLMENT}/{REF.server}/a/b', f'{ENROLLMENT}/{REF.server}/%ZZ', f'{ENROLLMENT}/{REF.server}/%FF', f'{ENROLLMENT}/{REF.server}/%0A', f'{ENROLLMENT}/{REF.server}/' + 'a' * 257]:
        with pytest.raises(ValueError):
            parse_tmux_reference(text)


def test_create_poll_resolve_use_exact_paths_and_bind_requested_name():
    requests = []

    def handle(req):
        requests.append(req)
        assert req.headers['Authorization'] == 'Bearer synthetic-test-token'
        return httpx.Response(202 if req.method == 'POST' else 200, json=COMPLETE if '/sessions' in req.url.path or '/operations/' in req.url.path else RESOLVED)

    api = client(handle).tmux
    assert api.create(ENROLLMENT, request_id=REQUEST_ID, name='work')['directive'] == REF.directive()
    assert api.operation(ENROLLMENT, REQUEST_ID, expected_name='work')['status'] == 'succeeded'
    assert api.resolve(REF.directive())['reference'] == REF.as_dict()
    assert [(r.method, r.url.path) for r in requests] == [
        ('POST', f'/terminal-references/{ENROLLMENT}/tmux/sessions'),
        ('GET', f'/terminal-references/{ENROLLMENT}/tmux/operations/{REQUEST_ID}'),
        ('GET', f'/terminal-references/{ENROLLMENT}/tmux/{REF.server}'),
    ]
    assert json.loads(requests[0].content) == {'requestId': REQUEST_ID, 'name': 'work'}
    assert requests[2].url.params['name'] == REF.session
    with pytest.raises(TmuxError, match='response_mismatch'):
        api.create(ENROLLMENT, request_id=REQUEST_ID, name='different')
    with pytest.raises(TmuxError, match='response_mismatch'):
        api.operation(ENROLLMENT, REQUEST_ID, expected_name=None)


def test_omitted_name_is_distinct_from_explicit_name_and_receipt_is_sanitized():
    requests = []

    def handle(req):
        requests.append(req)
        return httpx.Response(200, json={**COMPLETE, 'requestedName': None, 'socketPath': '/private/socket', 'target': {**TARGET, 'socketPath': '/private/socket'}})

    result = client(handle).tmux.create(ENROLLMENT, request_id=REQUEST_ID)
    assert result['requestedName'] is None
    assert 'name' not in json.loads(requests[0].content)
    assert 'socketPath' not in result and 'socketPath' not in result['target']


@pytest.mark.parametrize('patch', [
    {'reference': {**REF.as_dict(), 'server': 'c' * 16}},
    {'target': {**TARGET, 'enrollmentId': 'd' * 24}},
    {'multiplexer_references': 0},
    {'observedAt': 'invalid'},
    {'reason': '\x1b[31mprivate'},
])
def test_resolution_binding_rejects_substitution(patch):
    with pytest.raises(TmuxError):
        client(lambda req: httpx.Response(200, json={**RESOLVED, **patch})).tmux.resolve(REF)


@pytest.mark.parametrize('patch', [
    {'requestId': 'different'}, {'requestedName': None},
    {'reference': {**REF.as_dict(), 'enrollment': 'd' * 24}},
    {'directive': ':tmux[other]'},
])
def test_creation_binding_rejects_substitution(patch):
    with pytest.raises(TmuxError):
        client(lambda req: httpx.Response(200, json={**COMPLETE, **patch})).tmux.create(ENROLLMENT, request_id=REQUEST_ID, name='work')


def test_conflict_unsupported_and_transport_failure_never_retry_or_attach():
    for outcome in ['terminal_reference_name_conflict', 'terminal_reference_unsupported', 'network']:
        requests = []

        def handle(req):
            requests.append(req)
            if outcome == 'network':
                raise httpx.ConnectError('private network diagnostic', request=req)
            return httpx.Response(409, json={'error': outcome, 'secret': 'not echoed'})

        with pytest.raises(TmuxError) as failure:
            client(handle).tmux.create(ENROLLMENT, request_id=REQUEST_ID, name='work')
        assert len(requests) == 1
        assert 'secret' not in str(failure.value) and 'private' not in str(failure.value)
        assert failure.value.code == ('terminal_transport_unavailable' if outcome == 'network' else outcome)


def test_invalid_creation_arguments_and_gateway_origins_fail_before_network():
    requests = []
    api = client(lambda req: requests.append(req)).tmux
    for kwargs in [{'request_id': 'not-uuid'}, {'request_id': 'AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE'}, {'request_id': REQUEST_ID, 'name': 'two words'}, {'request_id': REQUEST_ID, 'name': 'bad:window'}, {'request_id': REQUEST_ID, 'name': ''}]:
        with pytest.raises(ValueError):
            api.create(ENROLLMENT, **kwargs)
    with pytest.raises(ValueError):
        api.operation(ENROLLMENT, 'AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE')
    for origin in ['http://external.example', 'https://user:password@app.example', 'https://app.example/path', 'https://app.example/?token=x', 'https://app.example\r\nBad: header']:
        with pytest.raises(ValueError):
            api.attach_ticket('test', REF, origin=origin)
    assert requests == []


def test_attach_ticket_uses_only_typed_launch_and_explicit_gateway_origin():
    requests = []

    def handle(req):
        requests.append(req)
        return httpx.Response(200, json={'ticket': 't' * 43, 'path': '/terminal/stream', 'socketPath': '/private/socket'})

    ticket = client(handle, 'https://term.example').tmux.attach_ticket('test', REF, origin='https://app.example', cols=90, rows=30)
    assert ticket == {'ticket': 't' * 43, 'path': '/terminal/stream'}
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == 'https://term.example/namespaces/test/terminal/tickets'
    assert request.headers['Origin'] == 'https://app.example'
    assert json.loads(request.content) == {
        'targetId': f'enrollment-{ENROLLMENT}', 'cols': 90, 'rows': 30,
        'launch': {'kind': 'tmux-reference', 'enrollmentId': ENROLLMENT, 'serverId': REF.server, 'session': REF.session},
    }


def test_cached_list_validates_binding_and_does_not_trigger_discovery():
    requests = []

    def handle(req):
        requests.append(req)
        return httpx.Response(200, json={'hostId': HOST, 'enrollmentId': ENROLLMENT, 'snapshot': None, 'status': 'unknown'})

    result = client(handle).tmux.list('test', HOST, ENROLLMENT)
    assert result['snapshot'] is None
    assert [(r.method, r.url.path) for r in requests] == [('GET', f'/namespaces/test/hosts/{HOST}/enrollments/{ENROLLMENT}/multiplexer-sessions')]
    with pytest.raises(TmuxError, match='identity_mismatch'):
        client(handle).tmux.list('test', 'e' * 24, ENROLLMENT)


def test_redirect_does_not_forward_account_token():
    requests = []

    def handle(req):
        requests.append(req)
        return httpx.Response(307, headers={'Location': 'https://foreign.example/'}, json={'error': 'redirect_not_allowed'})

    with pytest.raises(TmuxError):
        client(handle).tmux.resolve(REF)
    assert len(requests) == 1 and requests[0].url.host == 'api.example'
