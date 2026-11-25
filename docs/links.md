# Link files

Saved web and DreamLake URLs are ordinary `.url` files in Project, Source, and
Bindr file lists. They use the shared file preview and the usual file actions.
There is no separate Links tab.

```python
from dreamlake import DreamLakeClient, encode_link_file, parse_link_file

client = DreamLakeClient()
files = client.sources.files('example', 'SOURCE_ID', prefix='references')
saved = files.write('Guide.url', encode_link_file('https://example.com/guide'), expected_revision=None)
print(parse_link_file(saved['content']))
current = files.read('Guide.url')
files.write(current['path'], encode_link_file('https://docs.dreamlake.ai/'), expected_revision=current['revision'])
```

`expected_revision=None` creates only; updating requires the revision from a read.
Use the Source's immutable ID and an explicit prefix. The usual permissions and
file revision checks apply. See `examples/saved_links.py` for a runnable example
using environment variables.

For a Project folder, write the helper's contents to a local `.url` file and use
the existing upload API:

```python
from pathlib import Path
import dreamlake as dl

path = Path('Guide.url')
with path.open('x', encoding='utf-8', newline='') as out:
    out.write(dl.encode_link_file('https://example.com/guide'))
result = dl.upload(str(path), project='research@example', prefix='/references')
print(result)
```

Link files contain `[InternetShortcut]` and one `URL=https://...` line. HTTP(S)
URLs without embedded credentials are accepted; queries and fragments are kept.
Link previews accept files up to 64 KiB. Saving does not fetch or index the target,
and it grants no access to that target. Some websites block embedding; the preview
includes an external open action.

The earlier `DreamLakeClient.links(...)`, `dreamlake.links(...)`, `SavedLinks`,
and `new_link_id` APIs remain for compatibility with resource URL sidecars.
They do not create files. Read existing records, upload corresponding `.url`
files and verify them before optionally removing the legacy records with their
current `expected_version`. No automatic migration or deletion occurs.
