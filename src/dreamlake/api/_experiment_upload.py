"""Multipart uploads with intact filenames and binary bytes."""
import hashlib
import json
import mimetypes
import uuid
from pathlib import Path
from urllib.parse import quote


def file_upload(path, *, name=None, prefix=None, folder_id=None, description=None,
                tags=None, metadata=None, content_type=None, stream=False, checksum=None, size_bytes=None):
    path = Path(path)
    filename = path.name if name is None else name
    if not filename or '/' in filename or '\\' in filename:
        raise ValueError('file name must be a nonempty single segment')
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            digest.update(chunk)
            size += len(chunk)
    if (checksum is not None and checksum != digest.hexdigest()) or (size_bytes is not None and size_bytes != size):
        raise ValueError('File changed after it was queued')
    fields = {'sizeBytes': str(size), 'checksum': digest.hexdigest()}
    for key, value in [('prefix', prefix), ('folderId', folder_id), ('description', description)]:
        if value is not None:
            fields[key] = value
    if tags:
        fields['tags'] = ','.join(tags)
    if metadata is not None:
        fields['metadata'] = json.dumps(metadata)
    ctype = content_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    boundary = 'dreamlake-' + uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    fallback = ''.join(c if 32 <= ord(c) < 127 and c not in '\\"' else '_' for c in filename)
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{fallback}"; '
                  f"filename*=UTF-8''{quote(filename, safe='')}\r\nContent-Type: {ctype}\r\n\r\n").encode())
    head = b''.join(parts)
    tail = f'\r\n--{boundary}--\r\n'.encode()
    headers = {'Content-Type': f'multipart/form-data; boundary={boundary}',
               'Content-Length': str(len(head) + size + len(tail))}
    if stream:
        return _FileBody(path, head, tail), headers
    return head + path.read_bytes() + tail, headers


class _FileBody:
    # Reiterable for the single safe pre-write 401 renewal; each attempt reopens the file.
    def __init__(self, path, head, tail):
        self.path, self.head, self.tail = path, head, tail

    def __iter__(self):
        yield self.head
        with self.path.open('rb') as source:
            yield from iter(lambda: source.read(1 << 20), b'')
        yield self.tail
