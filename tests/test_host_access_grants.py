import json

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api.hosts import HostConfigurationError, HostConflictError, HostError

HOST, ENROLLMENT, GRANT = 'a' * 24, 'b' * 24, 'c' * 64
# Shapes as returned by dreamlake-server src/hosts/access-grants.ts grantView/list/revoke.
VIEW = dict(id=GRANT, hostId=HOST, enrollmentId=ENROLLMENT, unixUser='geyang', machineId='machine',
            grantee=dict(id='user-2', username='alice', name='Alice'), grantedAt='2026-11-16T00:00:00.000Z')
ACTIVE = dict(VIEW, state='active')
PENDING = dict(VIEW, state='revoked', pendingConnections=2, pendingUntil='2026-11-16T00:30:00.000Z')
REVOKED = dict(revoked=True, id=GRANT, closedConnections=1, unconfirmedConnections=1,
               unconfirmedUntil='2026-11-16T00:30:00.000Z')
# Team shapes as agreed in the host-share design (`?grantees=all`, `{teamGrant}`).
TEAM = dict(id='f' * 24, slug='robotics', name='Robotics')
TEAM_GRANT = dict(id='d' * 64, hostId=HOST, enrollmentId=ENROLLMENT, unixUser='geyang', machineId='machine',
                  team=TEAM, grantedAt='2026-11-16T00:00:00.000Z')
LISTING = {'grants': [ACTIVE, PENDING], 'teamGrants': [dict(TEAM_GRANT, state='active')], 'teams': [TEAM]}


def fixture(handler):
    return DreamLakeClient(dl_url='https://fixture.test', token='synthetic', transport=httpx.MockTransport(handler))


def test_routes_bodies_and_server_responses_pass_through():
    calls = []
    def serve(req):
        calls.append(req)
        if req.method == 'GET':
            return httpx.Response(200, json=LISTING)
        if req.method == 'POST':
            return httpx.Response(201, json={'grant': VIEW})
        return httpx.Response(200, json=REVOKED)
    hosts = fixture(serve).hosts
    assert hosts.access_grants('fortyfive', HOST) == LISTING
    assert hosts.grant_access('fortyfive', HOST, enrollment_id=ENROLLMENT, grantee='@alice') == {'grant': VIEW}
    assert hosts.revoke_access('fortyfive', HOST, GRANT) == REVOKED
    base = f'/namespaces/fortyfive/hosts/{HOST}/access-grants'
    assert [(c.method, c.url.path) for c in calls] == [('GET', base), ('POST', base), ('DELETE', f'{base}/{GRANT}')]
    assert calls[0].url.query == b'grantees=all'
    # The username is sent as typed; the server strips one leading '@'.
    assert json.loads(calls[1].content) == {'enrollmentId': ENROLLMENT, 'grantee': '@alice'}
    assert calls[2].content == b''


def test_team_grant_sends_team_and_verifies_returned_team():
    calls = []
    def serve(req):
        calls.append(req)
        return httpx.Response(201, json={'teamGrant': TEAM_GRANT})
    hosts = fixture(serve).hosts
    assert hosts.grant_access('fortyfive', HOST, enrollment_id=ENROLLMENT, team='robotics') == {'teamGrant': TEAM_GRANT}
    assert json.loads(calls[0].content) == {'enrollmentId': ENROLLMENT, 'team': 'robotics'}
    with pytest.raises(HostError, match='list grants'):
        hosts.grant_access('fortyfive', HOST, enrollment_id=ENROLLMENT, team='other-team')
    assert len(calls) == 2


def test_old_server_never_reads_as_no_team_grants():
    # Without team support the list omits teamGrants; that is unknown, not "only you".
    old = fixture(lambda _: httpx.Response(200, json={'grants': []})).hosts
    with pytest.raises(HostError) as caught:
        old.access_grants('fortyfive', HOST)
    assert caught.value.code == 'host_team_grants_unsupported'
    # Its POST schema rejects `team` (Fastify validation code is not a server error code).
    calls = []
    def refuse(req):
        calls.append(req)
        return httpx.Response(400, json={'statusCode': 400, 'code': 'FST_ERR_VALIDATION', 'error': 'Bad Request'})
    with pytest.raises(HostError, match='nothing was granted.*may not support team') as caught:
        fixture(refuse).hosts.grant_access('fortyfive', HOST, enrollment_id=ENROLLMENT, team='robotics')
    assert caught.value.status == 400 and len(calls) == 1


def test_union_revoke_reports_sessions_kept_by_another_grant():
    kept = dict(revoked=True, id=GRANT, closedConnections=0, unconfirmedConnections=0, stillAuthorizedConnections=2)
    assert fixture(lambda _: httpx.Response(200, json=kept)).hosts.revoke_access('fortyfive', HOST, GRANT) == kept
    with pytest.raises(HostError, match='revoke again'):
        fixture(lambda _: httpx.Response(200, json=dict(kept, stillAuthorizedConnections=-1))).hosts.revoke_access(
            'fortyfive', HOST, GRANT)


def test_fully_closed_revoke_has_no_deadline():
    done = dict(revoked=True, id=GRANT, closedConnections=0, unconfirmedConnections=0)
    assert fixture(lambda _: httpx.Response(200, json=done)).hosts.revoke_access('fortyfive', HOST, GRANT) == done


@pytest.mark.parametrize('change', [
    {'unconfirmedUntil': None, 'unconfirmedConnections': 1},  # pending without a deadline
    {'unconfirmedConnections': 0},                              # deadline without pending
    {'revoked': False}, {'id': 'd' * 64}, {'closedConnections': True},
])
def test_unverifiable_revoke_is_not_reported_closed(change):
    body = {k: v for k, v in dict(REVOKED, **change).items() if v is not None}
    with pytest.raises(HostError, match='revoke again'):
        fixture(lambda _: httpx.Response(200, json=body)).hosts.revoke_access('fortyfive', HOST, GRANT)


@pytest.mark.parametrize('grant', [
    dict(VIEW, enrollmentId='e' * 24), dict(VIEW, hostId='e' * 24),
    dict(VIEW, grantee=dict(id='user-3', username='mallory', name=None)), {k: v for k, v in VIEW.items() if k != 'unixUser'},
])
def test_grant_for_other_enrollment_host_or_person_fails_closed(grant):
    with pytest.raises(HostError, match='list grants'):
        fixture(lambda _: httpx.Response(200, json={'grant': grant})).hosts.grant_access(
            'fortyfive', HOST, enrollment_id=ENROLLMENT, grantee='alice')


@pytest.mark.parametrize('grant', [dict(PENDING, pendingConnections=0), {k: v for k, v in PENDING.items() if k != 'pendingUntil'},
                                   dict(ACTIVE, state='pending'), dict(ACTIVE, hostId='e' * 24)])
def test_invalid_listing_fails_closed(grant):
    with pytest.raises(HostError):
        fixture(lambda _: httpx.Response(200, json=dict(LISTING, grants=[grant]))).hosts.access_grants('fortyfive', HOST)


@pytest.mark.parametrize('listing', [
    dict(LISTING, teamGrants=[dict(TEAM_GRANT, state='active', hostId='e' * 24)]),
    dict(LISTING, teamGrants=[dict(TEAM_GRANT, state='revoked')]),  # revoked without pending connections
    dict(LISTING, teamGrants=[dict(TEAM_GRANT, state='active', team=dict(id='f' * 24))]),
    dict(LISTING, teams=[dict(name='No id')]), {k: v for k, v in LISTING.items() if k != 'teams'},
])
def test_invalid_team_listing_fails_closed(listing):
    with pytest.raises(HostError, match='could not be verified'):
        fixture(lambda _: httpx.Response(200, json=listing)).hosts.access_grants('fortyfive', HOST)


def test_refusal_exposes_server_code_only_and_never_retries():
    calls = []
    def refuse(req):
        calls.append(req)
        return httpx.Response(409, json={'error': 'grantee_not_member', 'code': 'grantee_not_member', 'echo': 'alice private'})
    with pytest.raises(HostConflictError) as caught:
        fixture(refuse).hosts.grant_access('fortyfive', HOST, enrollment_id=ENROLLMENT, grantee='alice')
    assert (caught.value.status, caught.value.code) == (409, 'grantee_not_member')
    assert 'private' not in str(caught.value)
    assert len(calls) == 1


def test_invalid_input_never_sends_request():
    hosts = fixture(lambda _: pytest.fail('unexpected request')).hosts
    for kwargs in (dict(enrollment_id='B' * 24, grantee='alice'), dict(enrollment_id=ENROLLMENT, grantee='alice smith'),
                   dict(enrollment_id=ENROLLMENT, grantee='@'), dict(enrollment_id=ENROLLMENT),
                   dict(enrollment_id=ENROLLMENT, grantee='alice', team='robotics'),
                   dict(enrollment_id=ENROLLMENT, team='robotics/../x')):
        with pytest.raises(HostConfigurationError):
            hosts.grant_access('fortyfive', HOST, **kwargs)
    with pytest.raises(HostConfigurationError):
        hosts.revoke_access('fortyfive', HOST, '../' + GRANT)
    with pytest.raises(HostConfigurationError):
        hosts.access_grants('fortyfive', 'not-a-host')
