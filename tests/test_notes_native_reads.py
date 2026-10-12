import hashlib,json
import httpx,pytest
from dreamlake.api.notes import Note,NoteError
from dreamlake.api._client import DreamLakeClient
ID='507f1f77bcf86cd799439011'
def snap(text,revision='rtc:current'):
    return dict(note=ID,content=text,hash='sha256:'+hashlib.sha256(text.encode()).hexdigest(),revision=revision)
def make(handler):
    return Note(ID,namespace='ns',client=DreamLakeClient(dl_url='https://example.test',token='test',transport=httpx.MockTransport(handler)))

def test_text_verified_cache_and_explicit_refresh_keep_hash_domain():
    calls=[];current=[snap('😀\r\n')]
    def handler(req):
        calls.append(req)
        assert req.url.params['contract']=='v2'
        return httpx.Response(200,json=current[0])
    note=make(handler)
    assert note.text==note.text=='😀\r\n' and len(calls)==1
    assert note.etag=='"'+current[0]['hash'][7:]+'"'
    current[0]=snap('new','rtc:later')
    assert note.text=='😀\r\n'
    assert note.refresh().text=='new' and len(calls)==2

@pytest.mark.parametrize('status,bad',[(302,None),(202,None),(200,{'hash':'sha256:'+'0'*64}),(200,{'revision':''})])
def test_refresh_failure_preserves_prior_verified_cache(status,bad):
    initial=snap('initial');state=[initial];calls=[]
    def handler(req):
        calls.append(req)
        if len(calls)==1:return httpx.Response(200,json=initial)
        return httpx.Response(status,json=dict(initial,**(bad or {})),headers={'location':'https://other.test'})
    note=make(handler); assert note.text=='initial';old=note.etag
    with pytest.raises(NoteError):note.refresh()
    assert note._text=='initial' and note.etag==old and len(calls)==2


def test_diff_modern_envelope_retained_hash_readback_no_cache_adoption():
    base=snap('old');current=snap('new','rtc:ack');calls=[]
    def handler(req):
        calls.append(req)
        if req.url.params.get('since'):
            assert req.url.params['since']==base['hash'] and req.url.params['contract']=='v2'
            return httpx.Response(200,json=dict(note=ID,hash=current['hash'],revision=current['revision'],base=base['hash'],format='diff',patch='patch'))
        assert req.url.params['at']==current['revision']
        return httpx.Response(200,json=current)
    note=make(handler);note._text='priorcache';note._etag='"'+base['hash'][7:]+'"'
    assert note.diff()=='patch'
    assert (note._text,note._etag)==('priorcache','"'+base['hash'][7:]+'"') and len(calls)==2

@pytest.mark.parametrize('change',[{'note':'other'},{'base':'sha256:'+'0'*64},{'format':'inline-dff'},{'revision':''},{'hash':'bad'},{'patch':1}])
def test_diff_invalid_envelope_no_adoption_or_fallback(change):
    base=snap('old');calls=[]
    def handler(req):
        calls.append(req)
        value=dict(note=ID,hash=base['hash'],revision='rtc:ack',base=base['hash'],format='diff',patch='patch');value.update(change)
        return httpx.Response(200,json=value)
    note=make(handler);note._text='cache';note._etag='cached'
    with pytest.raises(NoteError):note.diff(since=base['hash'])
    assert len(calls)==1 and (note._text,note._etag)==('cache','cached')


def test_diff_rtc_reference_refused_before_io():
    note=Note(ID,namespace='ns',client=object())
    with pytest.raises(ValueError):note.diff(since='rtc:original')


def test_legacy_append_uses_legacy_body_on_cache_miss():
    calls=[]
    def handler(req):
        calls.append(req)
        assert 'contract' not in req.url.params
        if req.method=='GET':return httpx.Response(200,json={'text':'old','etag':'oldetag'})
        assert req.headers['if-match']=='oldetag' and json.loads(req.content)=={'text':'old\nnew'}
        return httpx.Response(200,json={'etag':'newetag'})
    assert make(handler).append('new',legacy=True)=='newetag'
    assert [r.method for r in calls]==['GET','PUT']


def test_refresh_does_not_move_existing_doc_original_baseline():
    initial=snap('Draft','rtc:original');later=snap('Human: Draft','rtc:later');calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json=initial if len(calls)==1 else later)
    note=make(handler);doc=note.read()
    assert note.text=='Draft' and len(calls)==1
    assert note.refresh().text=='Human: Draft'
    assert doc.original==doc.text=='Draft' and doc._snapshot.revision=='rtc:original'
    assert note.etag=='"'+later['hash'][7:]+'"'


def test_diff_status_or_retained_hash_mismatch_keeps_cache():
    base=snap('base');current=snap('current');calls=[]
    def handler(req):
        calls.append(req)
        if req.url.params.get('since'):
            return httpx.Response(200,json=dict(note=ID,hash=base['hash'],revision=current['revision'],base=base['hash'],format='diff',patch='patch'))
        return httpx.Response(200,json=current)
    note=make(handler);note._text='cache';note._etag='etag'
    with pytest.raises(NoteError,match='acknowledged source hash'):note.diff(since=base['hash'])
    assert (note._text,note._etag)==('cache','etag') and len(calls)==2


def test_explicit_legacy_context_selects_compatibility_before_transport():
    calls=[]
    def handler(req):
        calls.append(req)
        assert req.url.path.endswith('/diff') and req.url.params['context']=='0'
        assert 'contract' not in req.url.params
        return httpx.Response(200,json={'diff':'legacy context'})
    assert make(handler).diff(since='"'+'a'*64+'"',context=0,legacy=True)=='legacy context'
    assert len(calls)==1


def test_refresh_malformed_json_is_typed_no_cache_update():
    note=make(lambda req:httpx.Response(200,text='SECRET invalid json'))
    note._text='saved';note._etag='etag'
    with pytest.raises(NoteError,match='invalid snapshot') as error:note.refresh()
    assert 'SECRET' not in str(error.value) and (note._text,note._etag)==('saved','etag')

@pytest.mark.parametrize('status',[201,202,302,307])
def test_modern_diff_rejects_unexpected_success_or_redirect_without_followup(status):
    base=snap('base');calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(status,json=dict(note=ID,hash=base['hash'],revision='rtc:current',base=base['hash'],format='diff',patch=''),headers={'location':'https://other.test/SECRET'})
    with pytest.raises(NoteError,match='unexpected diff') as error:make(handler).diff(since=base['hash'])
    assert 'SECRET' not in str(error.value) and len(calls)==1


@pytest.mark.parametrize('context',[0,3,100])
def test_candidate_native_context_wire(context):
    original=snap('base');calls=[]
    def handler(req):
        calls.append(req)
        if req.url.params.get('since'):
            assert req.url.path.endswith('/body') and req.url.params['contract']=='v2'
            assert req.url.params.get('context')==(None if context==3 else str(context))
            return httpx.Response(200,json=dict(note=ID,hash=original['hash'],revision=original['revision'],base=original['hash'],format='diff',patch=''))
        assert req.url.params['at']==original['revision']
        return httpx.Response(200,json=original)
    assert make(handler).diff(since=original['hash'],context=context)=='' and len(calls)==2

@pytest.mark.parametrize('context',[-1,101,True,1.5,'3'])
def test_candidate_native_context_invalid_before_http(context):
    with pytest.raises(ValueError):Note(ID,namespace='ns',client=object()).diff(since=snap('base')['hash'],context=context)


def test_native_context_unsupported_backend_refuses_without_legacy_fallback():
    calls=[]
    def handler(req):
        calls.append(req);assert req.url.path.endswith('/body')
        return httpx.Response(400,json={'message':'context unsupported'})
    with pytest.raises(NoteError):make(handler).diff(since=snap('base')['hash'],context=0)
    assert len(calls)==1

@pytest.mark.parametrize('status',[201,202,302,307])
def test_native_nondefault_context_refuses_status_redirect_without_replay(status):
    original=snap('base');calls=[]
    def handler(req):
        calls.append(req);assert req.url.params['context']=='0'
        return httpx.Response(status,json=dict(note=ID,hash=original['hash'],revision=original['revision'],base=original['hash'],format='diff',patch=''),headers={'location':'https://other.test'})
    with pytest.raises(NoteError):make(handler).diff(since=original['hash'],context=0)
    assert len(calls)==1
