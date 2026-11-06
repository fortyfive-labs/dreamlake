# Artifact visibility

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
