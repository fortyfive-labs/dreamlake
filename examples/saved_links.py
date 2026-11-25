"""Create link files in a disposable managed Source using the ordinary file API."""
import os
from dreamlake import DreamLakeClient, encode_link_file, parse_link_file

client = DreamLakeClient(token=os.environ['DREAMLAKE_API_KEY'])
files = client.sources.files(os.environ['NAMESPACE'], os.environ['SOURCE_ID'], prefix=os.environ.get('PREFIX', 'link-example'))
saved = files.write('Guide.url', encode_link_file('https://example.com/guide'), expected_revision=None)
print(saved['path'], parse_link_file(saved['content']))
read = files.read('Guide.url')
files.write(read['path'], encode_link_file('https://docs.dreamlake.ai/'), expected_revision=read['revision'])
