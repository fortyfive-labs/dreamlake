# Annotation metadata

`DreamLakeClient.annotations` reads and updates annotation metadata without
importing DreamDB or opening a content lease.

```python
from dreamlake.api._client import DreamLakeClient

client = DreamLakeClient()
row = client.annotations.get("team", "original-name")
client.annotations.update("team", row["name"], title="Retail Task Annotation")
client.annotations.rename("team", row["name"], "retail-task-annotation")
```

`rename` reads a fresh identity and slug generation, then sends one guarded
PATCH. For a longer review flow, retain the observed snapshot and call:

```python
client.annotations.update(
    "team", row["name"], slug="retail-task-annotation",
    expected_resource_id=row["id"], expected_slug=row["slug"],
    expected_revision=row["slugRevision"],
)
```

Omitted fields stay unchanged. Titles and slugs are independent; identical titles
are allowed. Slugs must match `[a-z0-9][a-z0-9._-]{0,63}` exactly; the server does
not trim or lowercase them. Names and historical slugs are reserved per namespace.
Original and previous URLs keep resolving to the same resource under current
permissions. Namespace owners/members may rename URLs; edit-share recipients may
edit titles, but cannot rename URLs or change visibility. `get` and `update` accept
an optional `share` argument.

`AnnotationMetadataError` exposes `status` and `code`. Conflicts return 409 and are
never retried automatically. A server lacking slug fields is rejected by `rename`
without writing. Re-read after an uncertain write before deciding whether to retry.

Content handles from `Annotation.open(...)` provide `set_title(title)` and
`rename_slug(slug)`. Their immutable `name`, resource ID, DreamDB data/history,
filings and share settings stay unchanged. Opening through a new or old URL first
resolves the catalog's immutable storage name. Listing `AnnotationInfo` adds
`title`, `slug` and `slug_revision` without removing existing fields.

The [canonical REST contract](https://docs.dreamlake.ai/annotations/reference)
also covers ownership moves, upload credentials and old-link reservations.
