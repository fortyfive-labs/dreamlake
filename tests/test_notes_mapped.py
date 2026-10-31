"""Mapped reads, outline, find, diff and the selector-based edits of the facade.

The mapped read is the snapshot the CLI's `--view html` fetches
(`view=html&contentFormat=literal-markdown`): the root element's data-*
attributes are the contract. A synthetic snapshot built here follows the
server's `renderNoteHtmlSnapshot` attribute layout; the annotated `text` is
asserted against the server's/CLI's construction rule byte for byte.
"""
import hashlib
import json

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api._notes_facade import (
    DiffResult,
    FindResult,
    FocusedRead,
    InvalidLineRange,
    MappedElement,
    MappedRead,
    Outline,
    SelectionError,
)
from dreamlake.api.notes import NoteError, NoteSnapshot, PatchReceipt

NS = 'fortyfive'
ID = '6ae3f318608512b4ba34860c'
REV = 'rtc:' + 'c9' * 32
NEXT = 'rtc:' + 'ab' * 32

SRC = "# Title\n\nFirst para.\n\n## Sub\n\n- item one\n- item two\n"
assert len(SRC) == 52
ADDRESSES = [
    {"id": "s1", "data-char": "0:7", "data-lines": "1:1"},
    {"id": "s1.p1", "data-char": "9:20", "data-lines": "3:3", "data-section-id": "s1"},
    {"id": "s1.1", "data-char": "22:28", "data-lines": "5:5", "data-section-id": "s1"},
    {"id": "s1.1.l1", "data-char": "30:52", "data-lines": "7:8", "data-section-id": "s1.1"},
    {"id": "s1.1.l1.i1", "data-char": "30:40", "data-lines": "7:7", "data-section-id": "s1.1"},
    {"id": "s1.1.l1.i2", "data-char": "41:51", "data-lines": "8:8", "data-section-id": "s1.1"},
]


def sha(text):
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


def snap(text=SRC, revision=REV):
    return NoteSnapshot(ID, text, sha(text), revision)


def esc(value):
    return value.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')


def html_snapshot(source, addresses, *, note=ID, revision=REV, full_hash=None, source_type='markdown', literal=True,
                  scope=None, target_id=None, section_id=None, start=0, end=None, body='', offset_unit='unicode-code-point'):
    attrs = (f' data-note="{note}" data-hash="{full_hash or sha(source)}" data-revision="{revision}"'
             f' data-source-type="{source_type}" data-offset-unit="{offset_unit}"')
    if literal:
        attrs += f' data-content-format="literal-markdown" data-addresses="{esc(json.dumps(addresses))}"'
    attrs += f' data-source="{esc(source)}"'
    if scope:
        attrs += f' data-scope="{scope}"'
        if target_id:
            attrs += f' data-target-id="{target_id}"'
        if section_id:
            attrs += f' data-section-id="{section_id}"'
        attrs += f' data-source-start="{start}" data-source-end="{end}" data-source-hash="{sha(source)}"'
    return f'<!doctype html>\n<html lang="en"{attrs}><head><title>Note preview</title></head><body>{body}</body></html>'


ARTICLE = html_snapshot(SRC, ADDRESSES)
HEADER = f'<!-- notes-reading-view revision={REV} hash={sha(SRC)} scope=article; generated hints are not source -->\n'
TEXT = (HEADER
        + '<!-- s1 chars=0:7 lines=1:1 -->\n# Title\n\n'
        + '<!-- s1.p1 chars=9:20 lines=3:3 section=s1 -->\nFirst para.\n\n'
        + '<!-- s1.1 chars=22:28 lines=5:5 section=s1 -->\n## Sub\n\n'
        + '<!-- s1.1.l1 chars=30:52 lines=7:8 section=s1.1 -->\n<!-- s1.1.l1.i1 chars=30:40 lines=7:7 section=s1.1 -->\n- item one\n'
        + '<!-- s1.1.l1.i2 chars=41:51 lines=8:8 section=s1.1 -->\n- item two\n')


def handle(handler):
    return DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(handler)).notes(ID, namespace=NS)


def never(req):
    raise AssertionError(f'unexpected request {req.method} {req.url}')


def serve(calls, *, html=ARTICLE, source=SRC):
    """A fake note: v2 source reads, mapped reads and an acknowledging PATCH."""
    def handler(req):
        calls.append(req)
        params = dict(req.url.params)
        if req.method == 'PATCH':
            data = json.loads(req.content)
            return httpx.Response(200, json=dict(note=ID, mode=data['mode'], baseRevision=data['baseRevision'],
                                                 hash=sha('x'), revision=NEXT, outcome='accepted',
                                                 commandId=data.get('commandId'), replayed=False), request=req)
        if params.get('view') == 'html':
            return httpx.Response(200, text=html, headers={'content-type': 'text/plain; charset=utf-8'}, request=req)
        return httpx.Response(200, json=snap(source).to_dict(), request=req)
    return handler


# ── mapped reads ─────────────────────────────────────────────────────────────

def test_mapped_read_is_the_cli_html_snapshot_request_with_exact_source_and_addresses():
    calls = []
    got = handle(serve(calls)).read(view='mapped')
    (req,) = calls
    assert req.method == 'GET' and req.url.path == f'/namespaces/{NS}/notes/{ID}/body'
    assert dict(req.url.params) == {'contract': 'v2', 'view': 'html', 'contentFormat': 'literal-markdown'}
    assert isinstance(got, MappedRead) and not isinstance(got, NoteSnapshot)
    assert (got.note, got.hash, got.revision) == (ID, sha(SRC), REV)
    assert got.content == SRC and got.chars == (0, 52) and got.complete is True and got.scope == {'article': True}
    assert got.source_type == 'markdown' and got.next is None
    assert got.elements[1] == MappedElement(address='s1.p1', chars=(9, 20), lines=(3, 3), section='s1')
    assert [e.address for e in got.elements] == [a['id'] for a in ADDRESSES]
    assert got.element('s1.1.l1.i2').chars == (41, 51) and got.element('nope') is None


def test_mapped_text_is_the_reading_view_the_server_and_cli_produce():
    assert handle(serve([])).read(view='mapped').text == TEXT


def test_mapped_read_at_a_retained_revision_verifies_it():
    calls = []
    got = handle(serve(calls)).read(view='mapped', at=REV)
    assert dict(calls[0].url.params)['at'] == REV and got.revision == REV
    with pytest.raises(NoteError, match='retained'):
        handle(serve([], html=html_snapshot(SRC, ADDRESSES, revision=NEXT))).read(view='mapped', at=REV)


def test_section_read_is_scoped_partial_and_keeps_whole_source_identity():
    scoped = html_snapshot(SRC[22:52], ADDRESSES[2:], full_hash=sha(SRC), scope='s1.1', start=22, end=52)
    calls = []
    got = handle(serve(calls, html=scoped)).read(view='mapped', section='s1.1', at=REV)
    assert dict(calls[0].url.params) == {'contract': 'v2', 'at': REV, 'section': 's1.1', 'view': 'html', 'contentFormat': 'literal-markdown'}
    assert got.scope == {'section': 's1.1'} and got.chars == (22, 52) and got.content == SRC[22:52]
    assert got.hash == sha(SRC) and got.complete is False
    assert [e.address for e in got.elements] == ['s1.1', 's1.1.l1', 's1.1.l1.i1', 's1.1.l1.i2']
    assert got.text == (f'<!-- notes-reading-view revision={REV} hash={sha(SRC)} scope=s1.1; generated hints are not source -->\n'
                        '<!-- s1.1 chars=22:28 lines=5:5 section=s1 -->\n## Sub\n\n'
                        '<!-- s1.1.l1 chars=30:52 lines=7:8 section=s1.1 -->\n<!-- s1.1.l1.i1 chars=30:40 lines=7:7 section=s1.1 -->\n- item one\n'
                        '<!-- s1.1.l1.i2 chars=41:51 lines=8:8 section=s1.1 -->\n- item two\n')


def test_address_read_uses_the_tag_selector():
    scoped = html_snapshot(SRC[9:20], ADDRESSES[1:2], full_hash=sha(SRC), scope='tag', target_id='s1.p1', start=9, end=20)
    calls = []
    got = handle(serve(calls, html=scoped)).read(view='mapped', address='s1.p1')
    assert dict(calls[0].url.params) == {'contract': 'v2', 'tag': 's1.p1', 'view': 'html', 'contentFormat': 'literal-markdown'}
    assert got.scope == {'address': 's1.p1'} and got.content == 'First para.' and got.chars == (9, 20) and not got.complete


def test_mapped_lines_slice_the_article_locally_and_keep_elements_inside():
    calls = []
    got = handle(serve(calls)).read(view='mapped', lines=(7, 8))
    assert len(calls) == 1 and dict(calls[0].url.params)['view'] == 'html'
    assert got.scope == {'lines': [7, 8]} and got.chars == (30, 52) and got.content == '- item one\n- item two\n'
    assert [e.address for e in got.elements] == ['s1.1.l1', 's1.1.l1.i1', 's1.1.l1.i2'] and got.next is None and not got.complete
    assert got.text == (f'<!-- notes-reading-view revision={REV} hash={sha(SRC)} scope=lines 7:8; generated hints are not source -->\n'
                        '<!-- s1.1.l1 chars=30:52 lines=7:8 section=s1.1 -->\n<!-- s1.1.l1.i1 chars=30:40 lines=7:7 section=s1.1 -->\n- item one\n'
                        '<!-- s1.1.l1.i2 chars=41:51 lines=8:8 section=s1.1 -->\n- item two\n')
    part = handle(serve([])).read(view='mapped', lines=(3, 3))
    assert part.content == 'First para.\n' and part.chars == (9, 21) and [e.address for e in part.elements] == ['s1.p1'] and part.next == 4


def test_html_sourced_note_maps_from_block_elements_and_has_no_annotated_text():
    source = '<p>hello</p>\n<p>world</p>\n'
    html = html_snapshot(source, [], source_type='html', literal=False,
                         body='<p id="s1.p1" data-char="0:12" data-lines="1:1">hello</p><p id="s1.p2" data-char="13:25" data-lines="2:2">world</p>')
    got = handle(serve([], html=html)).read(view='mapped')
    assert got.source_type == 'html' and got.text is None and got.content == source
    assert got.elements == (MappedElement('s1.p1', (0, 12), (1, 1)), MappedElement('s1.p2', (13, 25), (2, 2)))


@pytest.mark.parametrize('html', [
    html_snapshot(SRC, ADDRESSES, full_hash=sha('tampered')),
    html_snapshot(SRC, ADDRESSES, note='507f1f77bcf86cd799439011'),
    html_snapshot(SRC, ADDRESSES, offset_unit='utf-16'),
    html_snapshot(SRC[22:52], ADDRESSES[2:], full_hash=sha(SRC), scope='s1.1', start=22, end=52),  # partial for a full read
    '<html data-note="x">not a snapshot</html>',
    'SECRET plain text',
])
def test_invalid_mapped_snapshots_are_refused(html):
    with pytest.raises(NoteError) as error:
        handle(serve([], html=html)).read(view='mapped')
    assert 'SECRET' not in str(error.value)


def test_mapped_read_requires_200():
    def handler(req):
        return httpx.Response(302, text=ARTICLE, headers={'location': 'https://other.test/SECRET'}, request=req)
    with pytest.raises(NoteError) as error:
        handle(handler).read(view='mapped')
    assert 'SECRET' not in str(error.value)


# ── outline ──────────────────────────────────────────────────────────────────

def test_outline_reads_the_toc_scope_and_derives_heading_levels():
    toc = [dict(ADDRESSES[0], **{'data-section-id': 's1', 'data-source': '# Title'}),
           dict(ADDRESSES[2], **{'data-section-id': 's1.1', 'data-source': '## Sub'})]
    html = html_snapshot('', toc, full_hash=sha(SRC), scope='toc', start=0, end=0)
    calls = []
    got = handle(serve(calls, html=html)).outline(at=REV)
    assert dict(calls[0].url.params) == {'contract': 'v2', 'at': REV, 'toc': 'true', 'view': 'html', 'contentFormat': 'literal-markdown'}
    assert isinstance(got, Outline) and (got.note, got.hash, got.revision) == (ID, sha(SRC), REV)
    assert [(e.address, e.title, e.level, e.chars, e.lines, e.section) for e in got.entries] == [
        ('s1', '# Title', 1, (0, 7), (1, 1), 's1'), ('s1.1', '## Sub', 2, (22, 28), (5, 5), 's1.1')]
    assert got.text == (f'<!-- notes-reading-view revision={REV} hash={sha(SRC)} scope=toc; generated hints are not source -->\n'
                        '<!-- s1 chars=0:7 lines=1:1 section=s1 -->\n# Title\n<!-- s1.1 chars=22:28 lines=5:5 section=s1.1 -->\n## Sub')


# ── find ─────────────────────────────────────────────────────────────────────

def test_find_is_a_literal_search_of_the_exact_source_at_one_revision():
    calls = []
    got = handle(serve(calls, source='Draft one\nDraft two\ndraft three\n')).find(text='Draft', at=REV)
    assert dict(calls[0].url.params) == {'contract': 'v2', 'at': REV}
    assert isinstance(got, FindResult) and got.revision == REV and got.query == 'Draft' and not got.truncated
    assert [(m.text, m.chars, m.lines) for m in got] == [('Draft', (0, 5), (1, 1)), ('Draft', (10, 15), (2, 2)), ('draft', (20, 25), (3, 3))]
    exact = handle(serve([], source='Draft one\nDraft two\ndraft three\n')).find(text='Draft', case_sensitive=True)
    assert len(exact) == 2 and exact.case_sensitive is True
    capped = handle(serve([], source='Draft one\nDraft two\ndraft three\n')).find(text='Draft', limit=1)
    assert len(capped) == 1 and capped.truncated is True
    for bad in ({'text': ''}, {'text': 'x', 'limit': 0}, {'text': 7}):
        with pytest.raises(ValueError):
            handle(never).find(**bad)


# ── diff ─────────────────────────────────────────────────────────────────────

def test_diff_since_a_revision_reads_its_retained_source_and_verifies_the_result():
    base, current = snap('old\n'), snap('new\n', NEXT)
    calls = []
    def handler(req):
        calls.append(req)
        params = dict(req.url.params)
        if params.get('since'):
            assert params == {'contract': 'v2', 'since': base.hash, 'format': 'diff'}
            return httpx.Response(200, json=dict(note=ID, hash=current.hash, revision=NEXT, base=base.hash, format='diff',
                                                 patch='-old\n+new\n'), request=req)
        return httpx.Response(200, json=(base if params.get('at') == REV else current).to_dict(), request=req)
    got = handle(handler).diff(since=REV)
    assert got == DiffResult(note=ID, base_revision=REV, base_hash=base.hash, revision=NEXT, hash=current.hash,
                             format='diff', patch='-old\n+new\n')
    assert [dict(r.url.params).get('at') for r in calls] == [REV, None, NEXT]
    assert handle(handler).diff(since=base).patch == '-old\n+new\n'
    with pytest.raises(ValueError):
        handle(never).diff(since=REV, format='unified')


def test_diff_refuses_an_inconsistent_envelope():
    base = snap('old\n')
    def handler(req):
        if dict(req.url.params).get('since'):
            return httpx.Response(200, json=dict(note=ID, hash=sha('new\n'), revision=NEXT, base=sha('other'), format='diff', patch=''), request=req)
        return httpx.Response(200, json=base.to_dict(), request=req)
    with pytest.raises(NoteError):
        handle(handler).diff(since=REV)


# ── replace / insert / remove ────────────────────────────────────────────────

def sent(calls):
    return [json.loads(r.content) for r in calls if r.method == 'PATCH']


def test_replace_lines_compiles_one_record_against_the_snapshot():
    calls = []
    receipt = handle(serve(calls)).replace(lines=(3, 3), text='Second para.\n', at=snap())
    assert isinstance(receipt, PatchReceipt) and receipt.revision == NEXT
    assert sent(calls) == [{'format': 'inline-dff', 'patch': '@@ chars 9:21 @@\n~ [-First para.\\n-]{+Second para.\\n+}\n',
                            'baseRevision': REV, 'mode': 'merge'}]
    assert [r.method for r in calls] == ['PATCH']


def test_replace_address_from_a_mapped_read_needs_no_extra_request():
    calls = []
    note = handle(serve(calls))
    mapped = note.read(view='mapped')
    note.replace(address='s1.p1', text='X', at=mapped, mode='exact')
    assert [r.method for r in calls] == ['GET', 'PATCH']
    assert sent(calls) == [{'format': 'inline-dff', 'patch': '@@ chars 9:20 @@\n~ [-First para.-]{+X+}\n', 'baseRevision': REV, 'mode': 'exact'}]


def test_replace_address_from_a_snapshot_reads_the_mapping_at_that_revision():
    calls = []
    handle(serve(calls)).replace(address='s1.p1', text='X', at=snap())
    assert [(r.method, dict(r.url.params).get('view'), dict(r.url.params).get('at')) for r in calls] == [('GET', 'html', REV), ('PATCH', None, None)]
    assert sent(calls)[0]['patch'] == '@@ chars 9:20 @@\n~ [-First para.-]{+X+}\n'


def test_unknown_address_is_a_selection_error_with_no_write():
    calls = []
    with pytest.raises(SelectionError) as error:
        handle(serve(calls)).replace(address='s9.p9', text='X', at=snap())
    assert error.value.outcome == 'invalid-usage' and [r.method for r in calls] == ['GET']


def test_selector_rules_for_replace_and_remove():
    note = handle(never)
    with pytest.raises(ValueError, match='exactly one'):
        note.replace(text='X', at=snap())
    with pytest.raises(ValueError, match='exactly one'):
        note.replace(text='X', lines=(1, 1), address='s1', at=snap())
    with pytest.raises(ValueError, match='exactly one'):
        note.remove(at=snap())
    with pytest.raises(TypeError):
        note.remove(lines=(1, 1), text='X', at=snap())
    with pytest.raises(InvalidLineRange):
        note.replace(lines=(9, 9), text='X', at=snap())
    with pytest.raises(InvalidLineRange):
        note.remove(lines=(3, 2), at=snap())


def test_identical_replacement_is_a_local_no_write():
    receipt = handle(never).replace(lines=(3, 3), text='First para.\n', at=snap())
    assert receipt.outcome == 'no-write' and receipt.revision == REV and receipt.hash == sha(SRC)


def test_insert_placements():
    calls = []
    note = handle(serve(calls))
    mapped = note.read(view='mapped')
    note.insert(after='s1.p1', text='\n\nNew', at=mapped)
    note.insert(before='s1.p1', text='Lead ', at=mapped)
    note.insert(cursor=0, text='^', at=snap())
    note.insert(cursor=52, text='$', at=snap())
    assert [c['patch'] for c in sent(calls)] == ['@@ chars 20:20 @@\n~ {+\\n\\nNew+}\n', '@@ chars 9:9 @@\n~ {+Lead +}\n',
                                               '@@ chars 0:0 @@\n~ {+^+}\n', '@@ chars 52:52 @@\n~ {+$+}\n']
    assert all(c['mode'] == 'merge' for c in sent(calls))


def test_insert_placement_rules():
    note = handle(never)
    with pytest.raises(SelectionError, match='past the end'):
        note.insert(cursor=53, text='x', at=snap())
    for bad in (-1, True, 1.5):
        with pytest.raises(ValueError):
            note.insert(cursor=bad, text='x', at=snap())
    with pytest.raises(ValueError, match='exactly one'):
        note.insert(text='x', at=snap())
    with pytest.raises(ValueError, match='exactly one'):
        note.insert(before='s1', after='s1', text='x', at=snap())


def test_edits_against_a_focused_read_stay_inside_it():
    focused = FocusedRead(note=ID, hash=sha(SRC), revision=REV, scope={'lines': [7, 8]}, lines=(7, 8), chars=(30, 52),
                          content='- item one\n- item two\n')
    calls = []
    note = handle(serve(calls))
    note.replace(lines=(8, 8), text='- item 2\n', at=focused)
    note.insert(cursor=41, text='> ', at=focused)
    assert [c['patch'] for c in sent(calls)] == ['@@ chars 41:52 @@\n~ [-\\- item two\\n-]{+\\- item 2\\n+}\n', '@@ chars 41:41 @@\n~ {+> +}\n']
    with pytest.raises(SelectionError, match='outside'):
        handle(never).replace(lines=(3, 3), text='x', at=focused)
    with pytest.raises(SelectionError, match='outside'):
        handle(never).insert(cursor=10, text='x', at=focused)
    section = MappedRead(note=ID, hash=sha(SRC), revision=REV, scope={'section': 's1.1'}, chars=(22, 52), content=SRC[22:52],
                         source_type='markdown', elements=(), complete=False)
    with pytest.raises(SelectionError, match='complete snapshot'):
        handle(never).remove(lines=(7, 7), at=section)


def test_remove_deletes_exactly_the_span_with_canonical_escaping():
    calls = []
    note = handle(serve(calls))
    note.remove(lines=(7, 8), at=snap())
    mapped = note.read(view='mapped')
    note.remove(address='s1.1.l1.i2', at=mapped, command_id='cmd-conformance-0001')
    patches = sent(calls)
    assert patches[0] == {'format': 'inline-dff', 'patch': '@@ chars 30:52 @@\n~ [-\\- item one\\n\\- item two\\n-]\n',
                          'baseRevision': REV, 'mode': 'merge'}
    assert patches[1]['patch'] == '@@ chars 41:51 @@\n~ [-\\- item two-]\n' and patches[1]['commandId'] == 'cmd-conformance-0001'


# ── partial baselines and addresses ──────────────────────────────────────────

def test_address_outside_a_partial_mapped_read_is_refused_not_resolved_from_the_whole_note():
    # Regression: lines 7-8 observed chars 30:52, yet remove(address="s1.p1")
    # fetched the whole mapping and deleted chars 9:20.
    calls = []
    note = handle(serve(calls))
    part = note.read(view='mapped', lines=(7, 8))
    with pytest.raises(SelectionError, match='outside the partial read'):
        note.remove(address='s1.p1', at=part)
    with pytest.raises(SelectionError, match='outside the partial read'):
        note.insert(after='s1.p1', text='x', at=part)
    assert [r.method for r in calls] == ['GET']  # the read itself; no second mapping, no PATCH
    note.remove(address='s1.1.l1.i2', at=part)   # inside the observed span: fine
    assert sent(calls)[-1]['patch'] == '@@ chars 41:51 @@\n~ [-\\- item two-]\n'


def test_focused_line_read_cannot_baseline_an_address_edit():
    focused = FocusedRead(note=ID, hash=sha(SRC), revision=REV, scope={'lines': [7, 8]}, lines=(7, 8), chars=(30, 52),
                          content='- item one\n- item two\n')
    with pytest.raises(SelectionError, match='line-scoped'):
        handle(never).replace(address='s1.1.l1.i2', text='x', at=focused)


def test_complete_mapped_read_decides_unknown_addresses_without_another_request():
    calls = []
    note = handle(serve(calls))
    mapped = note.read(view='mapped')
    with pytest.raises(SelectionError, match='outside the mapping'):
        note.replace(address='s9.p9', text='x', at=mapped)
    assert [r.method for r in calls] == ['GET']


# ── outline heading levels ───────────────────────────────────────────────────

@pytest.mark.parametrize('source,tag,level', [
    ('# A', None, 1), ('  ## B', None, 2), ('   ### C', None, 3), ('###### F', None, 6),
    ('    # code block', None, 0), ('#NoSpace', None, 0), ('####### seven', None, 0),
    ('Title\n=====', None, 1), ('Title\n---', None, 2), ('Title\n- not an underline', None, 0),
    ('whatever', 'h3', 3), ('', 'H2', 2), ('# A', 'h4', 4),
])
def test_heading_level_from_tag_or_markdown(source, tag, level):
    from dreamlake.api._notes_facade import _heading_level
    assert _heading_level(source, tag) == level


def test_outline_levels_for_setext_indented_and_html_headings():
    toc = [{'id': 's1', 'data-char': '0:11', 'data-lines': '1:2', 'data-section-id': 's1', 'data-source': 'Title\n====='},
           {'id': 's1.1', 'data-char': '13:21', 'data-lines': '4:4', 'data-section-id': 's1.1', 'data-source': '  ## Sub'}]
    got = handle(serve([], html=html_snapshot('', toc, full_hash=sha(SRC), scope='toc', start=0, end=0))).outline()
    assert [(e.address, e.level) for e in got.entries] == [('s1', 1), ('s1.1', 2)]
    html = html_snapshot('', [], source_type='html', literal=False, full_hash=sha('<h2>Hello</h2>'), scope='toc', start=0, end=0,
                         body='<h2 id="s1" data-char="0:14" data-lines="1:1" data-section-id="s1">Hello <em>there</em></h2>'
                              '<h4 id="s1.1" data-char="15:30" data-lines="2:2">Deep</h4>')
    got = handle(serve([], html=html)).outline()
    assert [(e.address, e.title, e.level) for e in got.entries] == [('s1', 'Hello there', 2), ('s1.1', 'Deep', 4)]
