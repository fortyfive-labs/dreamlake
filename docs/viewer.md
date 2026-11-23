# 3D Preview references

`dreamlake.api` builds `:viewer[...]` references: the 3D Preview of a bundle
entry, a library asset or a scene version, as the DreamLake app, the CLI
(`dl viewer`) and the built-in agent use them. The grammar is the same
everywhere:

| Reference | Previews |
|---|---|
| `ns/bundle@N#entry` | a bundle entry at version N (preferred) |
| `ns/bundle#entry` | a bundle entry, latest version |
| `library:ns/lib#asset` | a library asset |
| `scene:ns/name@N` | a scene version (`scene:ns/name` for the latest) |

```python
from dreamlake.api import parse_viewer_ref, viewer_directive, viewer_href, viewer_layout_request

ref = parse_viewer_ref("fortyfive/teleop-berry@1#scene")   # None when invalid
viewer_directive(ref)     # ':viewer[fortyfive/teleop-berry@1#scene]' for Memo or Chat text
viewer_href(ref)          # '/fortyfive/bundles/teleop-berry/v/1/entries/scene'

# Open the Preview panel in a browser layout (as `dl viewer open` does): a
# revision-guarded request for view kind `asset-preview` with only { ref }.
request = viewer_layout_request(ref, destination="REGION_ID", if_revision=7)
```

- `@latest` is not accepted: omit the version to follow the latest.
- Pin the version you checked before you write a directive into a Memo.
- A reference grants no access. The page resolves it as the signed-in user, and
  the layout request carries no credentials or share tokens.
