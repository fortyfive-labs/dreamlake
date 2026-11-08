# Memos

Memos are shared documents for people and agents. From Python 0.41.0, use the
canonical `memos` facade and `Memo` types:

```python
import dreamlake as dl
from dreamlake.api.memos import Memo, MemoSnapshot

# Opening a handle is local; read() makes the request.
memo = dl.memos("team/6ae3f318608512b4ba34860c")
snapshot = memo.read()
print(snapshot.content)
```

An explicit client provides the same interface:

```python
from dreamlake.api._client import DreamLakeClient

client = DreamLakeClient()
memo = client.memos("6ae3f318608512b4ba34860c", namespace="team")
snapshot = memo.read()
receipt = memo.append(text="\nReviewed.", at=snapshot, mode="merge")
```

Use the snapshot returned by the original read as the edit baseline. The rename
does not change command identity, failure outcomes, file
permissions, or the exact source submitted for an edit.

Collection operations are `dl.memos.list(namespace="team")` and
`dl.memos.create(namespace="team", name="Review")`. The lower-level API exposes
`memo`, `create_memo`, `list_memos`, `search_memos`, and `grep_memos` from either
`dreamlake` or `dreamlake.api.memos`.

## Phased compatibility

`dl.notes`, `client.notes`, `Note`, and the old function names remain aliases.
Imports from `dreamlake.api.notes`, `_notes_facade`, and `_notes_mapped` resolve
to the canonical implementations; there is one editing engine. New code should
use `memos`, `Memo`, and `dreamlake.api.memos`.

This SDK release uses the existing server routes and response fields. Physical
database names and RTC wire changes are separate coordinated rollout steps.
Existing `:note[...]` content remains readable. Use ordinary links for new Memo
references; this release introduces no `:memo` directive.

## Baseline references

Python 0.41.2 accepts complete room-bound `memo-rtc:v1:` references as well as
historical native `rtc:` revisions. Treat either as opaque: carry the original
snapshot through edits, pinned reads and command reconciliation. Do not truncate
a reference or substitute the content hash. The matching server decides whether
a saved draft belongs to its current collaboration room; preserve the draft and
read a fresh baseline if it reports a session change. Publishing this SDK does
not activate the server's RTC migration.

## Visibility

Memos support `private`, `unlisted` and `public`. Unlisted memos are readable by
anyone with their direct URL, without signing in, but stay out of public profiles
and discovery. Private memos require existing access; Public memos are listed.

```python
memo = dl.memos.create(namespace="team", name="Review", visibility="unlisted")
memo.set_visibility(visibility="unlisted")
assert memo.metadata().visibility == "unlisted"
```

`dl.notes` remains an alias. `set_visibility` reads current management permission
before sending one metadata-only PATCH. It preserves content, history, attachments
and share links. It never retries an uncertain write; read metadata before deciding
whether to try again. Only callers with `permissions.setVisibility` may change it.
Changing visibility does not revoke existing share grants or grant anonymous editing.

## Public article URLs

For public or unlisted memos, get or allocate a URL from the saved H1, then edit
its public slug independently of the internal slug or display name:

```python
memo = dl.memos("<namespace>/<memo-id>")
current = memo.permalink()
changed = memo.permalink(slug="my-article")
print(changed["href"], changed["aliases"])
```

The dictionary contains `noteId`, `namespaceSlug`, `slug`, `href` (relative to the
web app), and `aliases` (previous slugs). Metadata management permission is
required. Title edits preserve the URL. Old slugs remain reserved aliases within
the namespace; `MemoPermalinkConflict` means an explicit slug is already reserved.
Choose another slug rather than retrying. Automatic allocation suffixes collisions.
Copies allocate fresh URLs without inheriting alias history. Private visibility
revokes anonymous access through current and previous URLs. These operations use
the same REST permalink endpoint as the web UI and TypeScript SDK.

## Presence and selections

Read the live roster without joining or renewing a lease:

```python
roster = memo.presence(selections=True)
print(roster.status, roster.selection_status, roster.observed_at)
if roster.selection_status == "available":
    print(roster.selection_hash, roster.participants)
```

`selections` defaults to false and requires a bool. Matching servers return
`status` (`available` or `inactive`), `selection_status` (`available`, `inactive`
or `not_requested`), and `selection_hash` when source-bound selections are
available. Older servers yield `unknown` / `unsupported`. Transport and access
failures raise; they never become an empty successful roster.

Participant dictionaries retain the server's `selection`: null, unresolved, or
resolved with `anchor`, `head`, `start`, `end`, `unit`, `text` and `truncated`.
Offsets are Unicode code points at `selection_hash`. A collapsed selection is a
caret. The preview may be truncated; selection data does not prove attention or
human edit authorship. Use `memo.diff(since=prior_revision)` for recent source
changes and retain the normal source snapshot/revision guards for edits.
