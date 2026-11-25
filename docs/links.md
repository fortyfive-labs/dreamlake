# Saved Links

Save web and DreamLake URLs on a resource with the same API used by its Links tab.
Readers can list links; current content editors can change them. Saving a URL
does not retrieve its contents or grant access to its destination.

```python
import os
from dreamlake import DreamLakeClient, new_link_id

client = DreamLakeClient(token=os.environ['DREAMLAKE_API_KEY'])
links = client.links(os.environ['NAMESPACE'], 'project', os.environ['PROJECT_ID'])
link_id = new_link_id()  # retain before sending, so an uncertain create can be read
saved = links.create('https://example.com/guide', title='Guide', link_id=link_id)['link']
changed = links.update(saved['id'], expected_version=saved['version'], description='Getting started')['link']
page = links.list(limit=25)
print(page['links'])
while page['nextCursor']:
    page = links.list(cursor=page['nextCursor'], limit=25)
    print(page['links'])
links.remove(changed['id'], expected_version=changed['version'])
```

Use immutable resource IDs and the current owning namespace. Kinds: `project`,
`bindr`, `node` (file/folder), `source`, `note`, `artifact`, `annotation`,
`workflow`, `env` (Scene), `repo`, `runnable`, `library`, `bundle`, and `session`.
Sources have one list across folders. Links follow a renamed or transferred resource.

Updates and removal require the read version. On HTTP `409`, read again and
reconcile changes rather than overwriting. No mutation is automatically retried.
For an uncertain creation, call `links.get(link_id)`; reuse the same ID and
identical content if retrying. A different payload at that ID is a conflict.
See the [product contract](https://docs.dreamlake.ai/links) for fields and limits.

Available in dreamlake 0.59.0 and a server with the Links API.
