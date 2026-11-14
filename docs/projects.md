# Project names and URL slugs

Use the explicit owning namespace and a verified project ID or current slug.
`inspect` returns `id`, `name`, `slug`, `slugMode`, and `slugRevision`.

```python
from dreamlake.api._client import DreamLakeClient

client = DreamLakeClient()
project = client.projects.inspect("my-team", "research")
renamed = client.projects.update("my-team", project["id"], name="Research Results")
custom = client.projects.update("my-team", project["id"], slug="stable-research")
automatic = client.projects.update("my-team", project["id"], slug_mode="auto")
```

Name-only changes update the slug when mode is `auto`; custom slugs persist.
Passing `slug` selects custom mode. Resetting with `slug_mode="auto"` derives
the slug immediately and follows future name changes. It cannot be combined
with `slug`. New projects without explicit slugs default to auto; older projects
without saved preferences retain custom mode until explicitly reset.

`update` inspects the target, then PATCHes its stable ID with
`expectedProjectId` and `expectedSlugRevision`. You may supply
`expected_project_id` and `expected_slug_revision` from an earlier read to reject
a stale edit. The server requires Project ADMIN and commits name, slug and
paths atomically. Collisions return 409 without a numbered fallback or partial
rename. The client never automatically retries a mutation.

Old browser URLs continue to locate the same project under current permissions;
historical slugs are reserved for it. Integrations should retain stable IDs.
An unsupported server fails before writing. After a timeout or
`PROJECT_UPDATE_UNCONFIRMED`, inspect the project ID before deciding whether to
retry. The returned `slug` is the canonical address.
