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
