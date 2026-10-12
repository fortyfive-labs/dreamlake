from dataclasses import FrozenInstanceError
import hashlib
import httpx,pytest
from dreamlake.api.notes import Note,NoteSnapshot,NoteError
from dreamlake.api._client import DreamLakeClient
ID='507f1f77bcf86cd799439011'
def snap(text):return NoteSnapshot(ID,text,'sha256:'+hashlib.sha256(text.encode()).hexdigest(),'rtc:original')
def make(handler):return Note(ID,namespace='ns',client=DreamLakeClient(dl_url='https://example.test',token='test',transport=httpx.MockTransport(handler)))
def env(original,text,start,end,line,last,total,truncated):return dict(note=ID,revision=original.revision,hash=original.hash,scope='lines',text=text,range=dict(start=start,end=end),sliceHash=snap(text).hash,startLine=line,endLine=last,totalLines=total,truncated=truncated)

@pytest.mark.parametrize('source,text,start,end,line,last,total,truncated',[
    ('😀\r\nsecond\nlast','second\n',3,10,2,2,3,True),
    ('😀\r\nsecond\n','second\n',3,10,2,2,2,False),
    ('first\nlast','last',6,10,2,2,2,False),
])
def test_native_partial_exact_unicode_crlf_and_readonly(source,text,start,end,line,last,total,truncated):
    original=snap(source);calls=[]
    def handler(req):
        calls.append(req);assert req.url.params['at']==original.revision and req.url.params['contract']=='v2'
        return httpx.Response(200,json=env(original,text,start,end,line,last,total,truncated))
    note=make(handler);note._text='existingcache';note._etag='etag'
    part=note.read_lines(line,last,at=original)
    assert part.text==text and part.baseline==original and part.start==start and part.end==end
    assert part.truncated==truncated and not hasattr(part,'save')
    assert (note._text,note._etag)==('existingcache','etag') and len(calls)==1
    with pytest.raises(FrozenInstanceError):part.text='overwrite'

@pytest.mark.parametrize('change',[{'note':'other'},{'revision':'wrong'},{'hash':'sha256:'+'0'*64},{'sliceHash':'bad'},{'scope':'note'},{'text':'wrong'},{'range':{'start':True,'end':2}},{'startLine':True},{'totalLines':3},{'truncated':True}])
def test_partial_bad_envelope_rejected_without_cache(change):
    original=snap('a\nb');value=env(original,'b',2,3,2,2,2,False);value.update(change)
    note=make(lambda req:httpx.Response(200,json=value));note._text='cache';note._etag='etag'
    with pytest.raises(NoteError):note.read_lines(2,at=original)
    assert (note._text,note._etag)==('cache','etag')

@pytest.mark.parametrize('status',[201,202,302,307])
def test_partial_status_redirect_refused(status):
    original=snap('a\nb');calls=[]
    def handler(req):calls.append(req);return httpx.Response(status,json=env(original,'b',2,3,2,2,2,False),headers={'location':'https://other.test'})
    with pytest.raises(NoteError):make(handler).read_lines(2,at=original)
    assert len(calls)==1

@pytest.mark.parametrize('source,total',[('',0),('a\n',1),('a\nb',2)])
def test_no_range_full_readonly_view_not_partial_cache(source,total):
    original=snap(source);note=Note(ID,namespace='ns',client=object())
    part=note.read_lines(at=original)
    assert part.text==source and part.total_lines==total and not part.truncated and part.end==len(source)
    assert note._text is None and note._etag is None

@pytest.mark.parametrize('args',[(True,None),(0,None),(3,None),(2,1)])
def test_invalid_range_refused_before_http(args):
    with pytest.raises(ValueError):Note(ID,namespace='ns',client=object()).read_lines(*args,at=snap('a\nb'))


def test_range_end_clamps_and_retains_final_lf():
    original=snap('a\n😀\r\nlast\n');text='😀\r\nlast\n'
    def handler(req):
        assert req.url.params['endLine']=='99'
        return httpx.Response(200,json=env(original,text,2,len(original.content),2,3,3,False))
    part=make(handler).read_lines(2,99,at=original)
    assert part.text==text and part.end_line==3 and not part.truncated


def test_wrong_full_snapshot_rejected_before_transport():
    wrong=NoteSnapshot('other','a',snap('a').hash,'rtc:original')
    with pytest.raises(ValueError):Note(ID,namespace='ns',client=object()).read_lines(1,at=wrong)


def test_partial_read_preserves_existing_doc_baseline_and_full_cache():
    original=snap('Draft\nsecond');calls=[]
    def handler(req):
        calls.append(req)
        if req.url.params.get('startLine'):
            return httpx.Response(200,json=env(original,'second',6,12,2,2,2,False))
        return httpx.Response(200,json=original.to_dict())
    note=make(handler);doc=note.read();part=note.read_lines(2,at=original)
    assert part.text=='second' and doc._snapshot==original and doc.original=='Draft\nsecond'
    assert note.text=='Draft\nsecond' and len(calls)==2
