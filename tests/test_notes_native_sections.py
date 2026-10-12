"""Private proposed endpoint contract: no actual backend capability claim."""
import hashlib,json
import httpx,pytest
from dreamlake.api.notes import Note,NoteSnapshot,SectionSnapshot,NoteError,PatchReceipt
from dreamlake.api._client import DreamLakeClient
ID='507f1f77bcf86cd799439011'
SOURCE='😀 preamble\r\n## Draft\r\nDraft gap Draft\r\n### Child\r\nchild\r\n## Next\r\nlast'
START=SOURCE.index('## Draft');END=SOURCE.index('## Next')
def snapshot(source=SOURCE,revision='rtc:original'):
    return NoteSnapshot(ID,source,'sha256:'+hashlib.sha256(source.encode()).hexdigest(),revision)
ORIGINAL=snapshot()
SCOPE=SectionSnapshot('draft',SOURCE[START:END],START,END,ORIGINAL)
def envelope(scope=SCOPE):
    return dict(note=ID,hash=scope.baseline.hash,revision=scope.baseline.revision,anchor=scope.anchor,text=scope.text,range=dict(start=scope.start,end=scope.end))
def make(handler):
    return Note(ID,namespace='ns',client=DreamLakeClient(dl_url='https://example.test',token='test',transport=httpx.MockTransport(handler)))
def ack(req,source=SOURCE,revision='rtc:ack'):
    mode=json.loads(req.content)['mode'];return httpx.Response(200,json=dict(note=ID,hash=snapshot(source).hash,revision=revision,baseRevision=ORIGINAL.revision,mode=mode))

def test_saved_section_write_disjoint_local_plan_no_get_preserves_outside_and_gap():
    calls=[]
    def handler(req):
        calls.append(req);assert req.method=='PATCH'
        data=json.loads(req.content)
        assert data['baseRevision']==ORIGINAL.revision
        assert ' gap ' not in data['patch'] and 'Next' not in data['patch'] and 'Child' not in data['patch']
        assert data['patch'].count('@@ chars')==3
        assert '@@ chars 0:' not in data['patch']
        return ack(req)
    assert isinstance(make(handler).write_section('draft',SCOPE.text.replace('Draft','Done'),at=SCOPE),PatchReceipt)
    assert len(calls)==1


def test_pinned_section_read_full_baseline_binding():
    calls=[]
    def handler(req):
        calls.append(req);assert req.url.params['at']==ORIGINAL.revision and req.url.params['contract']=='v2'
        assert req.url.path.endswith('/sections/draft')
        return httpx.Response(200,json=envelope())
    assert make(handler).read_section_snapshot('draft',at=ORIGINAL)==SCOPE and len(calls)==1

@pytest.mark.parametrize('change',[{'note':'other'},{'revision':'rtc:wrong'},{'hash':'sha256:'+'0'*64},{'anchor':'other'},{'text':'different'},{'range':{'start':True,'end':END}},{'range':{'start':START,'end':len(SOURCE)+1}}])
def test_invalid_scope_refused_without_patch(change):
    calls=[]
    def handler(req):
        calls.append(req);return httpx.Response(200,json=dict(envelope(),**change))
    with pytest.raises(NoteError):make(handler).write_section('draft','replacement',at=ORIGINAL)
    assert len(calls)==1 and calls[0].method=='GET'

@pytest.mark.parametrize('status',[201,202,302,307])
def test_scope_requires200_no_redirect_fallback(status):
    calls=[]
    def handler(req):
        calls.append(req);return httpx.Response(status,json=envelope(),headers={'location':'https://other.test'})
    with pytest.raises(NoteError):make(handler).read_section('draft',at=ORIGINAL)
    assert len(calls)==1


def test_nonlast_replace_ensures_lf_but_last_does_not(monkeypatch):
    from dreamlake.api import _character_plan
    real=_character_plan.character_diff;targets=[]
    def observe(base,target,**kwargs):
        targets.append(target);return real(base,target,**kwargs)
    monkeypatch.setattr(_character_plan,'character_diff',observe)
    note=make(lambda req:ack(req))
    note.write_section('draft','replacement',at=SCOPE)
    last=SectionSnapshot('next',SOURCE[END:],END,len(SOURCE),ORIGINAL)
    note.write_section('next','replacement',at=last)
    assert targets==['replacement\n','replacement']


def test_delete_exact_subtree_range():
    def handler(req):
        data=json.loads(req.content)
        assert data['patch'].startswith(f'@@ chars {START}:{END} @@')
        assert 'Child' in data['patch'] and 'Next' not in data['patch'] and 'preamble' not in data['patch']
        return ack(req)
    make(handler).delete_section('draft',at=SCOPE)


def test_insert_after_subtree_ack_outline_and_utf16_compatibility():
    updated=SOURCE[:END]+'## Added\n'+SOURCE[END:];acknowledged=snapshot(updated,'rtc:ack');calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='PATCH':
            assert json.loads(req.content)['patch']==f'@@ chars {END}:{END} @@\n~ {{+## Added\\n+}}\n'
            return ack(req,updated)
        assert req.url.params['at']=='rtc:ack'
        if req.url.path.endswith('/body'):return httpx.Response(200,json=acknowledged.to_dict())
        return httpx.Response(200,json=dict(note=ID,revision='rtc:ack',hash=acknowledged.hash,sections=[dict(anchor='draft',title='Draft',level=2,range=dict(start=START,end=END))]))
    result=make(handler).insert_section('## Added',after='draft',at=SCOPE)
    assert result[0].start==START+1  # astral prefix: legacy UTF16 fields remain UTF16.
    assert [r.method for r in calls]==['PATCH','GET','GET']


def test_failed_post_ack_outline_retains_receipt_and_fences_resubmission():
    updated=SOURCE+'\n## Added\n';acknowledged=snapshot(updated,'rtc:ack');calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='PATCH':return ack(req,updated)
        if req.url.path.endswith('/body'):return httpx.Response(200,json=acknowledged.to_dict())
        return httpx.Response(503,json={'message':'not deployed'})
    note=make(handler)
    with pytest.raises(NoteError,match='acknowledged') as error:note.insert_section('## Added',at=ORIGINAL)
    assert error.value.receipt.revision=='rtc:ack'
    count=len(calls)
    with pytest.raises(NoteError,match='reconcile'):note.insert_section('## Added',at=ORIGINAL)
    with pytest.raises(NoteError,match='reconcile'):note.delete_section('draft',legacy=True)
    assert len(calls)==count

@pytest.mark.parametrize('options',[{'legacy':True,'at':ORIGINAL},{'force':True,'exact':True}])
def test_mixed_scope_options_rejected_before_http(options):
    note=Note(ID,namespace='ns',client=object())
    with pytest.raises(ValueError):note.write_section('draft','new',**options)
    with pytest.raises(ValueError):note.delete_section('draft',**options)
    with pytest.raises(ValueError):note.insert_section('new',**options)


def test_bad_saved_source_refused_before_http():
    invalid=SectionSnapshot('draft','wrong',START,END,ORIGINAL)
    with pytest.raises(NoteError):Note(ID,namespace='ns',client=object()).write_section('draft','new',at=invalid)


def test_default_read_uses_one_full_snapshot_then_pinned_section():
    calls=[]
    def handler(req):
        calls.append(req)
        if req.url.path.endswith('/body'):return httpx.Response(200,json=ORIGINAL.to_dict())
        assert req.url.params['at']==ORIGINAL.revision
        return httpx.Response(200,json=envelope())
    assert make(handler).read_section('draft')==SCOPE.text and len(calls)==2

@pytest.mark.parametrize('rows',[
    [dict(anchor='x',title='x',level=2,range=dict(start=True,end=END))],
    [dict(anchor='x',title='x',level=7,range=dict(start=START,end=END))],
    [dict(anchor='x',title='x',level=2,range=dict(start=START,end=END))]*2,
])
def test_outline_invalid_ranges_or_metadata_refused(rows):
    def handler(req):return httpx.Response(200,json=dict(note=ID,hash=ORIGINAL.hash,revision=ORIGINAL.revision,sections=rows))
    with pytest.raises(NoteError):make(handler).sections(at=ORIGINAL)


def test_exact_unchanged_section_submits_empty_patch():
    def handler(req):
        body=json.loads(req.content)
        assert body['mode']=='exact' and body['patch']==''
        return ack(req)
    assert make(handler).write_section('draft',SCOPE.text,at=SCOPE,exact=True).mode=='exact'

@pytest.mark.parametrize('kwargs',[{'before':'','after':'draft'},{'before':'','at':ORIGINAL}])
def test_ambiguous_or_empty_placement_refused_before_io(kwargs):
    with pytest.raises(ValueError):Note(ID,namespace='ns',client=object()).insert_section('new',**kwargs)


def test_zero_saved_named_section_cannot_be_replacement_insertion_before_http():
    zero=SectionSnapshot('draft','',START,START,ORIGINAL)
    with pytest.raises(NoteError,match='nonempty'):
        Note(ID,namespace='ns',client=object()).write_section('draft','fake replacement',at=zero)


def test_zero_backend_named_section_refused_before_patch():
    value=dict(envelope(),text='',range=dict(start=START,end=START));calls=[]
    def handler(req):calls.append(req);return httpx.Response(200,json=value)
    with pytest.raises(NoteError,match='nonempty'):make(handler).write_section('draft','fake replacement',at=ORIGINAL)
    assert len(calls)==1 and calls[0].method=='GET'


def test_zero_named_outline_scope_refused():
    value=dict(note=ID,hash=ORIGINAL.hash,revision=ORIGINAL.revision,sections=[dict(anchor='draft',title='Draft',level=2,range=dict(start=START,end=START))])
    with pytest.raises(NoteError,match='nonempty'):make(lambda req:httpx.Response(200,json=value)).sections(at=ORIGINAL)
