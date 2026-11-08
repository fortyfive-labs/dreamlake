# Artifact visibility and sharing

Python 0.41.1 adds `DreamLakeClient.artifacts` for catalog reads and visibility
management. It uses the same saved login or `DREAMLAKE_API_KEY` as other SDK APIs.

```python
from dreamlake import DreamLakeClient

client = DreamLakeClient()
client.artifacts.set_visibility("my-team", "report", "unlisted")
artifact = client.artifacts.get("my-team", "report")
assert artifact["visibility"] == "unlisted"
print(client.artifacts.list("my-team"))
```

`set_visibility(namespace, artifact, visibility)` accepts `private`, `public`,
and `unlisted`. Unlisted permits anonymous direct-link reads while hiding the
artifact from public profiles and feeds. Namespace members still see it in their
catalog. This is not private access: anyone with the URL can open it.

The method checks that the artifact exists and `viewerCanManage` is true before
sending a visibility-only update. It preserves content, version history, share
tokens and other metadata. Errors are not retried automatically. After a timeout
or other uncertain result, call `get` before deciding whether to retry.

The importable Python CLI also accepts `--visibility unlisted` on
`python -m dreamlake.cli artifact push`. The standalone CLI supports both push
and `dreamlake artifact visibility report unlisted` starting with 0.104.8.

## Share only the most recent version

Python 0.42.1 adds a metadata-only policy setter:

```python
client.artifacts.set_share_latest_only("my-team", "report", True)
artifact = client.artifacts.get("my-team", "report")
assert artifact["shareLatestOnly"] is True
# Allow recipients to browse history again:
client.artifacts.set_share_latest_only("my-team", "report", False)
```

The method requires a boolean and namespace management permission. It resolves
renamed URL slugs to immutable IDs before writing and confirms the returned
policy. It changes neither the share token, visibility nor stored versions.
The policy limits nonmember readers (including public, unlisted, share-link and
Project readers) to the latest registered version. Namespace members retain
history. Existing links follow subsequent pushes; already downloaded content
cannot be recalled. This setting does not itself grant access.

The importable Python CLI accepts `--latest-only` and `--no-latest-only` on
`python -m dreamlake.cli artifact push`. Omitting both preserves the existing
policy. For an existing artifact without an upload, use the SDK method above or
standalone CLI `dreamlake artifact share latest-only report on` (0.110.1+).
After an uncertain write, call `get` to reconcile before retrying.
