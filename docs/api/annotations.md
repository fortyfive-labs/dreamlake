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

## Sharing and visibility

Visibility and signed-in link access are independent. Private requires existing
permission; unlisted permits anonymous reads at the known URL without public
catalog discovery; public also appears in public listings. Namespace managers
control these settings. Read-link recipients can read content, and write-link
recipients can also edit titles and obtain scoped content-write credentials.
Neither role grants sharing management, URL renaming or visibility changes.

```python
api = client.annotations
settings = api.get_share("team", "original-name")
api.set_share("team", "original-name", role="write")
api.update("team", "original-name", visibility="unlisted")
recipients = api.share_access("team", "original-name")
```

A signed-in active user accepts the live invitation with
`api.get("team", "original-name", share=invitation_token)`. Later requests can
omit the token: retained acceptance follows the current role while the link is
enabled. Content handles may then open the same annotation using that identity.

`api.revoke_share(namespace, name)` disables link-derived access, preserving
acceptance records. Re-enabling creates a new token and restores retained
recipients at the current role. Pass `revoke_accepted=True` to remove those
records too. `api.remove_share_access(namespace, name, user_id)` removes one
acceptance, but a still-valid link can admit that user again; removal is not a
block. Public or unlisted visibility continues to allow anonymous reads even
when the invitation is disabled. Already issued URLs and scoped credentials
remain valid until expiry.

These metadata methods never retry writes automatically or upload content.
`Annotation.set_visibility("unlisted")` is also supported. New public and
unlisted `VideoAnnotation` datasets omit absolute source paths from episode
metadata; changing visibility does not rewrite existing episode content.
