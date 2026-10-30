"""`note.read(lines=(a, b))`: a focused read that cannot pass for the whole note.

The request is the v2 source read; the slice and its code-point coordinates
are computed here. Offsets are Unicode code points — what `@@ chars` and the
CLI address — so CJK and emoji are one each, never UTF-16 units or bytes.
"""
import hashlib
import json

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api._notes_facade import FocusedRead, SnapshotRequired, _line_chars
from dreamlake.api.notes import NoteSnapshot

NS = 'fortyfive'
ID = '6ae3f318608512b4ba34860c'
REV = 'rtc:' + 'c9' * 32


def snap(text):
    return NoteSnapshot(ID, text, 'sha256:' + hashlib.sha256(text.encode()).hexdigest(), REV)


def handle(text, calls=None):
    def handler(req):
        if calls is not None:
            calls.append(req)
        return httpx.Response(200, json=snap(text).to_dict(), request=req)
    return DreamLakeClient(dl_url='https://example.test', token='test',
                           transport=httpx.MockTransport(handler)).notes(f'{NS}/{ID}')


def test_focused_read_is_the_same_request_as_read_with_a_distinct_result():
    calls = []
    got = handle('a\nb\nc\n', calls).read(lines=(1, 1))
    (req,) = calls
    assert req.method == 'GET' and req.url.path == f'/namespaces/{NS}/notes/{ID}/body'
    assert dict(req.url.params) == {'contract': 'v2'}
    assert isinstance(got, FocusedRead) and not isinstance(got, NoteSnapshot)
    assert got.to_dict() == {'note': ID, 'hash': snap('a\nb\nc\n').hash, 'revision': REV,
                             'lines': [1, 1], 'chars': [0, 2], 'content': 'a\n', 'complete': False, 'next': 2}


def test_hash_and_revision_are_of_the_whole_source_not_the_slice():
    got = handle('a\nb\nc\n').read(lines=(2, 3))
    assert got.content == 'b\nc\n' and got.hash == snap('a\nb\nc\n').hash
    assert got.hash != 'sha256:' + hashlib.sha256(b'b\nc\n').hexdigest()
    assert got.complete is False and got.next is None


@pytest.mark.parametrize('content,lines,chars,sliced,following', [
    # CJK and emoji are one code point each; a flag is two (regional indicators).
    ('日本語\n😀 smile\n🇯🇵 end', (2, 2), (4, 12), '😀 smile\n', 3),
    ('日本語\n😀 smile\n🇯🇵 end', (3, 3), (12, 18), '🇯🇵 end', None),
    ('日本語\n😀 smile\n🇯🇵 end', (1, 3), (0, 18), '日本語\n😀 smile\n🇯🇵 end', None),
    # CRLF: the CR stays inside its line, so the slice is byte-exact.
    ('a\r\nb\r\n', (1, 1), (0, 3), 'a\r\n', 2),
    ('a\r\nb\r\n', (2, 2), (3, 6), 'b\r\n', None),
    # No trailing newline on the last line.
    ('a\nb', (2, 2), (2, 3), 'b', None),
    ('abc', (1, 1), (0, 3), 'abc', None),
    # Empty lines are lines.
    ('\n\nx\n', (2, 2), (1, 2), '\n', 3),
    # End past the last line is clamped; the result says what it covered.
    ('a\nb\nc\n', (2, 99), (2, 6), 'b\nc\n', None),
])
def test_line_to_code_point_conversion(content, lines, chars, sliced, following):
    assert content[chars[0]:chars[1]] == sliced
    got = handle(content).read(lines=lines)
    assert got.chars == chars and got.content == sliced and got.next == following
    assert got.lines == (lines[0], min(lines[1], content.count('\n') + (not content.endswith('\n'))))


def test_offsets_are_code_points_not_utf16_or_bytes():
    content = '😀\nx'
    got = handle(content).read(lines=(2, 2))
    assert got.chars == (2, 3)  # UTF-16 would say (3, 4); UTF-8 bytes (5, 6)
    assert len(content.encode('utf-16-le')) // 2 == 4 and len(content.encode()) == 6


@pytest.mark.parametrize('bad', [(0, 1), (2, 1), (1,), (1, 2, 3), [1, 'b'], (True, 1), 'a:b', 1, (1.0, 2.0)])
def test_lines_must_be_two_integers_one_based(bad):
    with pytest.raises(ValueError):
        _line_chars('a\nb\n', bad)


@pytest.mark.parametrize('content,start', [('a\nb\n', 3), ('', 1), ('abc', 2)])
def test_start_past_the_last_line_is_an_error_not_an_empty_page(content, start):
    with pytest.raises(ValueError, match='past the last line'):
        _line_chars(content, (start, start))


def test_focused_read_at_a_retained_revision():
    calls = []
    got = handle('a\nb\n', calls).read(lines=(1, 1), at=REV)
    assert dict(calls[0].url.params) == {'contract': 'v2', 'at': REV} and got.revision == REV


def test_focused_read_cannot_be_a_whole_body_write_or_append_baseline():
    focused = handle('a\nb\nc\n').read(lines=(1, 1))
    def never(req):
        raise AssertionError('no write expected')
    note = DreamLakeClient(dl_url='https://example.test', token='test',
                           transport=httpx.MockTransport(never)).notes(f'{NS}/{ID}')
    with pytest.raises(SnapshotRequired, match='lines 1-1'):
        note.write(text='whole\n', at=focused, mode='exact')
    with pytest.raises(SnapshotRequired):
        note.append(text='more', at=focused, mode='exact')
    # It is also not a NoteSnapshot, so the existing methods refuse it too.
    from dreamlake.api.notes import Note
    with pytest.raises(ValueError):
        Note(ID, namespace=NS, client=object()).write('whole\n', at=focused)


def test_focused_read_supports_a_targeted_patch_at_its_chars():
    focused = handle('a\nb\nc\n').read(lines=(2, 2))
    assert focused.chars == (2, 4) and focused.content == 'b\n'
    body = f'@@ chars {focused.chars[0]}:{focused.chars[1] - 1} @@\n~ [-b-]{{+B+}}\n'
    def handler(req):
        if req.method == 'GET':
            assert req.url.params['at'] == REV
            return httpx.Response(200, json=snap('a\nb\nc\n').to_dict(), request=req)
        data = json.loads(req.content)
        assert data['patch'] == body and data['baseRevision'] == REV and data['mode'] == 'exact'
        return httpx.Response(200, json=dict(note=ID, mode='exact', baseRevision=REV, hash=snap('a\nB\nc\n').hash,
                                             revision='rtc:' + 'ab' * 32), request=req)
    note = DreamLakeClient(dl_url='https://example.test', token='test',
                           transport=httpx.MockTransport(handler)).notes(f'{NS}/{ID}')
    assert note.patch(patch=body, at=focused, mode='exact').revision == 'rtc:' + 'ab' * 32
    assert note.read(at=focused).revision == REV  # a focused read also names a revision to read at
