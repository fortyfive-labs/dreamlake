import json
import httpx
import pytest
from dreamlake.vault import Vault, VaultError


def entry(name):
    return dict(name=name,type='string',revision=1,value='SENSITIVE',encrypted='SENSITIVE')


def test_list_drains_pages_and_page_returns_continuation_without_plaintext():
    calls=[]
    def handle(request):
        calls.append(request)
        second=request.url.params.get('cursor')=='next'
        return httpx.Response(200,json={'entries':[entry('alice/b' if second else 'alice/a')], 'nextCursor':None if second else 'next'})
    with httpx.Client(base_url='https://example.test',transport=httpx.MockTransport(handle)) as client:
        vault=Vault(client)
        assert [x['name'] for x in vault.list(prefix='alice',include_deleted=True)]==['alice/a','alice/b']
        assert all(x.url.params['limit']=='100' and x.url.params['prefix']=='alice' and x.url.params['includeDeleted']=='true' for x in calls)
        calls.clear()
        page=vault.list_page(prefix='alice',limit=1)
        assert page['nextCursor']=='next' and len(calls)==1
        assert 'SENSITIVE' not in json.dumps(page)
        assert vault.list_page(prefix='alice',limit=1,cursor=page['nextCursor'])['nextCursor'] is None


def test_legacy_server_list_remains_complete():
    with httpx.Client(base_url='https://example.test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'entries':[entry('alice/b'),entry('alice/a')]}))) as client:
        assert len(Vault(client).list(prefix='alice'))==2


@pytest.mark.parametrize('body',[
    {'entries':[],'nextCursor':'next'},
    {'entries':[entry('alice/a')],'nextCursor':42},
    {'entries':[entry('alice/b'),entry('alice/a')],'nextCursor':None},
    {'entries':[entry('alice/a')],'nextCursor':'same'},
])
def test_bad_pages_fail_without_partial_results(body):
    with httpx.Client(base_url='https://example.test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body))) as client:
        with pytest.raises(VaultError):Vault(client).list(prefix='alice')


def test_page_invalid_options_make_no_request():
    with httpx.Client(base_url='https://example.test',transport=httpx.MockTransport(lambda r:pytest.fail('unexpected request'))) as client:
        vault=Vault(client)
        for limit in [0,201,True,'100',1.5]:
            with pytest.raises(VaultError):vault.list_page(limit=limit)
        for cursor in ['',{},'x/y']:
            with pytest.raises(VaultError):vault.list_page(cursor=cursor)
        with pytest.raises(VaultError):vault.list_page(include_deleted='true')


@pytest.mark.parametrize('change', ['origin', 'token', 'cookie'])
@pytest.mark.parametrize('next_cursor', ['next', None])
def test_list_aborts_before_return_or_next_page_if_connection_authority_changes(change, next_cursor):
    calls=[]
    def handle(request):
        calls.append(request)
        if change == 'origin':client.base_url='https://changed.test'
        if change == 'token':client.headers['Authorization']='Bearer other-account'
        if change == 'cookie':client.cookies.set('session','other-account')
        return httpx.Response(200,json={'entries':[entry('alice/a')], 'nextCursor':next_cursor})
    with httpx.Client(base_url='https://original.test',headers={'Authorization':'Bearer original-account'},transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(VaultError,match='connection changed'):
            Vault(client).list(prefix='alice')
    assert len(calls)==1
    assert calls[0].url.host=='original.test'
    assert calls[0].headers['Authorization']=='Bearer original-account'
