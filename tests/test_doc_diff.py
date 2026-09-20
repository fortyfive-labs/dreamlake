import pytest
from tests.test_doc import doc_of, MD
from dreamlake.api._diff import unified_diff
from dreamlake.api._editing import EditError


def test_last_edit_is_distinct_and_survives_save_and_noop():
    doc, _ = doc_of('one\ntwo\n')
    assert doc.diff(since='last_edit') == ''
    doc.replace('ONE', query='one')
    doc.replace('TWO', query='two')
    latest = doc.diff(since='last_edit')
    assert '-two\n+TWO\n' in latest
    assert '-one\n' not in latest
    assert '-one\n' in doc.diff()
    doc.replace('TWO', query='TWO')
    assert doc.diff(since='last_edit') == latest
    doc.save()
    assert doc.diff() == ''
    assert doc.diff(since='last_edit') == latest


def test_element_and_revert():
    doc, _ = doc_of('<p>old</p>\n')
    doc.select('p').replace('new', query='old')
    assert '+<p>new</p>' in doc.diff(since='last_edit')
    doc.revert()
    assert '+<p>old</p>' in doc.diff(since='last_edit')


@pytest.mark.parametrize('before,after', [
    ('', 'new'), ('old', ''), ('old', 'new'), ('old\n', 'new'),
    ('old', 'new\n'), ('a\r\nb\r\n', 'a\r\nc\r\n'),
    ('a\u2028b\n', 'c\u2028d\n'), ('你好\n', '世界\n'),
    ('a\nb\nc\nd\ne\nf\ng\nh\n', 'A\nb\nc\nd\ne\nf\ng\nH\n'),
])
@pytest.mark.parametrize('context', [0, 3])
def test_round_trip(before, after, context):
    doc, note = doc_of(before)
    patch = unified_diff(before, after, 'note', context)
    assert doc.patch(patch) == after
    assert note.writes == []
    assert doc.diff(since='last_edit') == doc.diff()
    doc.save()
    assert note.preconditions == ['rev-1']


@pytest.mark.parametrize('patch', [
    'garbage', '--- a\n+++ b\n', '@@ -1 +1 @@\n-wrong\n+new\n',
    '@@ -1,2 +1 @@\n-old\n+new\n', '@@ -1 +4 @@\n-old\n+new\n',
    '@@ -1 +1 @@\n-old\n+new\n--- other\n+++ other\n',
    '@@ -1 +1 @@\n-old\n+new\n@@ -2 +2 @@\n-missing\n+other\n',
])
def test_failure_is_atomic(patch):
    doc, note = doc_of('initial\n')
    doc.replace('old', query='initial')
    previous_diff = doc.diff(since='last_edit')
    with pytest.raises(EditError):
        doc.patch(patch)
    assert doc.text == 'old\n'
    assert doc.diff(since='last_edit') == previous_diff
    assert note.writes == []


def test_options_and_empty_patch():
    doc, _ = doc_of()
    with pytest.raises(ValueError):
        doc.diff(since='unknown')
    for context in [-1, True, 1.5]:
        with pytest.raises(ValueError):
            doc.diff(context=context)
    assert doc.patch('') == MD
    assert not doc.dirty


def test_jsdiff_preamble():
    doc, _ = doc_of('old\n')
    assert doc.patch('Index: note\n' + '=' * 67 + '\n--- note\n+++ note\n@@ -1,1 +1,1 @@\n-old\n+new\n') == 'new\n'


def test_patched_draft_retained_when_save_is_stale():
    from dreamlake.api.notes import NoteChanged
    doc, note = doc_of('old\n')
    doc.patch(unified_diff('old\n', 'new\n', 'note', 3))
    note.stale = True
    with pytest.raises(NoteChanged):
        doc.save()
    assert doc.text == 'new\n'
    assert doc.original == 'old\n'
    assert doc.dirty
    assert '+new\n' in doc.diff(since='last_edit')
