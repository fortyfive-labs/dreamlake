"""Run against a disposable project: NAMESPACE=... PROJECT_ID=... python examples/saved_links.py."""
import os
from dreamlake import DreamLakeClient, new_link_id

client = DreamLakeClient(token=os.environ['DREAMLAKE_API_KEY'])
links = client.links(os.environ['NAMESPACE'], 'project', os.environ['PROJECT_ID'])
link_id = new_link_id()
print('Creation recovery ID:', link_id, flush=True)
saved = links.create('https://example.com/guide', title='Example guide', link_id=link_id)['link']
updated = links.update(saved['id'], expected_version=saved['version'], description='Saved URL example')['link']
print(links.get(link_id)['link'])
page = links.list(limit=25)
while page['nextCursor']:
    page = links.list(limit=25, cursor=page['nextCursor'])
links.remove(link_id, expected_version=updated['version'])
