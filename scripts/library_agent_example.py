"""Run after the companion server release: python scripts/library_agent_example.py --output ./cup"""
import argparse
from pathlib import Path
from dreamlake import DreamLakeClient

parser = argparse.ArgumentParser()
parser.add_argument('--library', default='fortyfive/scanned-objects')
parser.add_argument('--query', default='coffee cup')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
api = DreamLakeClient().libraries
catalog = api.list(limit=50)
print('Catalog page:', len(catalog['libraries']), 'nextOffset:', catalog.get('nextOffset', 'legacy server'))
search = api.search([args.library], args.query, limit=3)
print('Search:', search['total'], 'candidates; inspecting at most three')
if not search['hits']:
    raise SystemExit('No candidates in this scope')
hit = search['hits'][0]
ref = hit['namespace'], hit['library'], hit['assetId']
requests = [{'id': 'search', 'operation': 'search', 'libraries': [args.library], 'q': args.query, 'limit': 3}]
requests += [{'id': str(i), 'operation': 'asset', **{k: h[k] for k in ('namespace', 'library', 'assetId')}} for i, h in enumerate(search['hits'])]
for result in api.batch(requests)['results']:
    print('Batch:', result['id'], 'ok' if result['ok'] else result['error'])
asset = api.asset(*ref)
print('Selected:', '/'.join(ref), 'kind:', asset['kind'], 'license:', asset['license'])
image = api.image(*ref)
args.output.mkdir(parents=True, exist_ok=True)
if image['status'] == 'ready':
    image_path = args.output / ('thumbnail.' + image['mediaType'].split('/')[1])
    image_path.write_bytes(image['content'])
    print('Image:', image_path, image['purpose'], len(image['content']), 'bytes')
else:
    print('Image:', image['status'])
files = api.download(*ref, args.output / 'asset', max_bytes=64 * 1024 * 1024)
print('Verified dependency closure:', len(files), 'files')
