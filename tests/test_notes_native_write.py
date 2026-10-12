import hashlib,json
import httpx,pytest
from dreamlake.api.notes import Note,NoteSnapshot,NoteError,NoteChanged
from dreamlake.api._client import DreamLakeClient
from dreamlake.api._character_plan import character_diff,NotePatchError
ID='507f1f77bcf86cd799439011'
def snap(text,rev='rtc:original'):
    return NoteSnapshot(ID,text,'sha256:'+hashlib.sha256(text.encode()).hexdigest(),rev)
def make(handler):
    return Note(ID,namespace='ns',client=DreamLakeClient(dl_url='https://example.test',token='test',transport=httpx.MockTransport(handler)))
def ack(req,text='merged',rev='rtc:ack'):
    data=json.loads(req.content)
    return httpx.Response(200,json=dict(note=ID,mode=data['mode'],baseRevision=data['baseRevision'],hash=snap(text).hash,revision=rev))

def test_write_original_disjoint_gap_preserved_without_get():
    original=snap('😀 Draft middle Draft\r\n'); calls=[]
    def handler(req):
        calls.append(req)
        assert req.method=='PATCH'
        body=json.loads(req.content)
        assert body['baseRevision']==original.revision
        assert ' middle ' not in body['patch']
        assert body['patch'].count('@@ chars')>=2
        return ack(req)
    receipt=make(handler).write('😀 Done middle Done\r\n',at=original)
    assert receipt.base_revision==original.revision and len(calls)==1

def test_doc_keeps_own_original_and_adopts_acknowledged_concurrent_source():
    original=snap('Draft middle Draft'); merged=snap('Human: Done middle Done','rtc:ack'); calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='GET':
            if req.url.params.get('at'):
                assert req.url.params['at']==merged.revision
                return httpx.Response(200,json=merged.to_dict())
            return httpx.Response(200,json=original.to_dict())
        assert json.loads(req.content)['baseRevision']==original.revision
        return ack(req,merged.content,merged.revision)
    note=make(handler); doc=note.read(); doc.replace('Done',query='Draft',all=True)
    note._etag='unrelated'; note._text='unrelated'
    receipt=doc.save()
    assert doc.text==doc.original==merged.content and not doc.dirty
    assert receipt.revision==merged.revision
    assert [r.method for r in calls]==['GET','PATCH','GET']

def test_failed_post_ack_readback_retains_receipt_and_draft_no_replay():
    original=snap('Draft'); calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='PATCH': return ack(req,'Done')
        if req.url.params.get('at'): return httpx.Response(503,json={'message':'unavailable'})
        return httpx.Response(200,json=original.to_dict())
    doc=make(handler).read(); doc.replace('Done',query='Draft')
    with pytest.raises(NoteError,match='write acknowledged') as error: doc.save()
    assert error.value.receipt.revision=='rtc:ack'
    assert doc.text=='Done' and doc.original=='Draft' and doc.dirty
    with pytest.raises(NoteError,match='reconcile'): doc.save()
    assert sum(r.method=='PATCH' for r in calls)==1

@pytest.mark.parametrize('status',[412,302,500])
def test_failed_patch_preserves_doc_original_draft(status):
    original=snap('Draft'); calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='GET':return httpx.Response(200,json=original.to_dict())
        return httpx.Response(status,json={'message':'failure'})
    doc=make(handler).read();doc.replace('Done',query='Draft')
    with pytest.raises(NoteError):doc.save(exact=True)
    assert doc.text=='Done' and doc.original=='Draft'
    assert [r.method for r in calls]==['GET','PATCH']

@pytest.mark.parametrize('base,target',[('aaaa','abaa'),('abab','baba'),('A'+'x'*5000+'B','C'+'x'*5000+'D'),('a'*2300,'b'*2300)])
def test_alignment_vectors(base,target):
    edits=character_diff(base,target); cursor=0;actual=''
    for e in edits:
        assert base[e['start']:e['end']]==e['expectedText']
        actual+=base[cursor:e['start']]+e['replacementText'];cursor=e['end']
    assert actual+base[cursor:]==target
    assert character_diff(base,target)==edits

@pytest.mark.parametrize('target',['\ud800','a'*1000001])
def test_invalid_unicode_size_rejected_before_io(target):
    with pytest.raises(NotePatchError):Note(ID,namespace='ns',client=object()).write(target)

def test_doc_second_save_uses_acknowledged_merged_original():
    initial=snap('Draft'); first=snap('Human: Done','rtc:first');second=snap('Human: Final','rtc:second');patches=[]
    def handler(req):
        if req.method=='GET':
            value={'rtc:first':first,'rtc:second':second}.get(req.url.params.get('at'),initial)
            return httpx.Response(200,json=value.to_dict())
        body=json.loads(req.content);patches.append(body)
        value=first if len(patches)==1 else second
        return ack(req,value.content,value.revision)
    doc=make(handler).read();doc.replace('Done',query='Draft');doc.save()
    doc.replace('Final',query='Done');doc.save()
    assert [p['baseRevision'] for p in patches]==[initial.revision,first.revision]
    assert 'Human' not in patches[1]['patch'] and doc.text==second.content


def test_noop_exact_patch_checked_without_readback_when_same_snapshot():
    initial=snap('same');methods=[]
    def handler(req):
        methods.append(req.method)
        if req.method=='GET':return httpx.Response(200,json=initial.to_dict())
        assert json.loads(req.content)['patch']=='' and json.loads(req.content)['mode']=='exact'
        return ack(req,initial.content,initial.revision)
    doc=make(handler).read(); receipt=doc.save(exact=True)
    assert receipt.mode=='exact' and methods==['GET','PATCH']

@pytest.mark.parametrize('kwargs',[{'force':True,'at':snap('x')},{'legacy':True,'exact':True},{'if_match':'etag','at':snap('x')}])
def test_write_mixed_options_rejected_before_io(kwargs):
    with pytest.raises(ValueError):Note(ID,namespace='ns',client=object()).write('y',**kwargs)


def test_work_limit_refuses_without_coarse_fallback():
    with pytest.raises(NotePatchError):
        Note(ID,namespace='ns',client=object()).write('b'*5000,at=snap('a'*5000))


def test_write_escapes_tab_before_native_inline_transport():
    def handler(req):
        body=json.loads(req.content)
        assert '\t' not in body['patch']
        assert r'\t' in body['patch']
        return ack(req,'a\tb')
    assert make(handler).write('a\tb',at=snap('ab')).revision=='rtc:ack'


@pytest.mark.parametrize('compatibility',[{'legacy':True},{'force':True}])
def test_modern_doc_legacy_save_clears_stale_native_baseline(compatibility):
    initial=snap('Draft');calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='GET':return httpx.Response(200,json=initial.to_dict())
        assert req.method=='PUT'
        return httpx.Response(200,json={'etag':'legacy-next'})
    doc=make(handler).read();doc.replace('Done',query='Draft');doc.save(**compatibility)
    assert doc.original=='Done' and doc._snapshot is None
    doc.replace('Final',query='Done');result=doc.save()
    assert result.etag=='legacy-next'
    assert [r.method for r in calls]==['GET','PUT','PUT']
    assert json.loads(calls[-1].content)=={'text':'Final'}
    assert calls[-1].headers['if-match']=='legacy-next'
