"""F2 synthetic HTTP/replay regressions; no external requests."""
import json

import httpx
import pytest

from dreamlake.vault import Vault, VaultError, VaultWriteError


@pytest.mark.parametrize('status', [400, 401, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504])
@pytest.mark.parametrize('explicit_id', [False, True])
def test_http_failure_never_proves_logical_write_rejected(status, explicit_id):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={'error': 'SYNTHETIC_DO_NOT_EMIT'})

    with httpx.Client(base_url='https://synthetic.invalid', transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(VaultWriteError) as raised:
            Vault(client).add('alice/probe', 'SYNTHETIC_DO_NOT_EMIT',
                              **({'request_id': 'synthetic-existing-id'} if explicit_id else {}))
    error = raised.value
    assert error.outcome == 'unknown'
    assert error.attempt_outcome == ('rejected' if 400 <= status < 500 and status != 408 else 'unknown')
    assert error.status == status and error.request_id == calls[0].headers['Idempotency-Key']
    assert len(calls) == 1
    assert 'SYNTHETIC_DO_NOT_EMIT' not in repr(error)


@pytest.mark.parametrize('code', ['IDEMPOTENCY_CONFLICT', 'WRITE_RECOVERY_EXPIRED'])
def test_committed_then_rejected_replay_preserves_uncertainty_until_matching_receipt(code):
    operation = None
    calls = []

    def handle(request):
        nonlocal operation
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'operation': operation})
        if operation is None:
            data = json.loads(request.content)
            operation = dict(requestId=request.headers['Idempotency-Key'], state='committed',
                             entry=dict(name=data['name'], type='string', revision=1),
                             committedAt='2030-01-01T00:00:00.000Z', retainUntil='2030-01-31T00:00:00.000Z')
            raise httpx.ReadTimeout('SYNTHETIC_DO_NOT_EMIT')
        return httpx.Response(409, json={'error': code})

    with httpx.Client(base_url='https://synthetic.invalid', transport=httpx.MockTransport(handle)) as client:
        vault = Vault(client)
        for attempt in range(2):
            with pytest.raises(VaultWriteError) as raised:
                vault.add('alice/probe', 'SYNTHETIC_VALUE', request_id='synthetic-existing-id')
            assert raised.value.outcome == 'unknown'
            assert raised.value.attempt_outcome == ('unknown' if attempt == 0 else 'rejected')
        assert len(calls) == 2
        assert vault.write_status(request_id='synthetic-existing-id')['state'] == 'committed'
        operation['requestId'] = 'synthetic-wrong-id'
        with pytest.raises(VaultError, match='Invalid vault write receipt'):
            vault.write_status(request_id='synthetic-existing-id')


@pytest.mark.parametrize('status', [404, 408, 409, 500])
def test_unavailable_receipt_does_not_resolve_unknown_write(status):
    with httpx.Client(base_url='https://synthetic.invalid',
                      transport=httpx.MockTransport(lambda req: httpx.Response(status))) as client:
        vault = Vault(client)
        with pytest.raises(VaultWriteError) as raised:
            vault.add('alice/probe', 'SYNTHETIC_VALUE', request_id='synthetic-existing-id')
        with pytest.raises(VaultError):
            vault.write_status(request_id=raised.value.request_id)
        assert raised.value.outcome == 'unknown'
