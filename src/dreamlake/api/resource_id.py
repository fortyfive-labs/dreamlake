"""
Resource ID encoding/decoding.

Format: {type_prefix}-{16 base62 chars}
Example: v-BV1bW411n7fY9x01

Type prefixes:
  v-  Video
  a-  Audio
  i-  Image
  lt- LabelTrack
  tt- TextTrack
"""

import re

_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ID_LEN = 16

_TYPE_PREFIXES = {
    "video": "v",
    "audio": "a",
    "image": "i",
    "label-track": "lt",
    "text-track": "tt",
}

_PREFIX_TO_TYPE = {v: k for k, v in _TYPE_PREFIXES.items()}

_RESOURCE_ID_RE = re.compile(r"^([a-z]{1,2})-([0-9A-Za-z]+)$")


def _bytes_to_base62(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    if n == 0:
        return _BASE62[0]
    chars = []
    while n > 0:
        n, r = divmod(n, 62)
        chars.append(_BASE62[r])
    return "".join(reversed(chars))


def _base62_to_bytes(s: str, length: int) -> bytes:
    n = 0
    for c in s:
        n = n * 62 + _BASE62.index(c)
    return n.to_bytes(length, "big")


def encode_resource_id(asset_type: str, object_id: str) -> str:
    prefix = _TYPE_PREFIXES.get(asset_type)
    if not prefix:
        raise ValueError(f"Unknown asset type: {asset_type}")
    raw = bytes.fromhex(object_id)
    b62 = _bytes_to_base62(raw)[:_ID_LEN].ljust(_ID_LEN, "0")
    return f"{prefix}-{b62}"


def decode_resource_id(resource_id: str) -> tuple[str, str]:
    m = _RESOURCE_ID_RE.match(resource_id)
    if not m:
        raise ValueError(f"Invalid resource ID: {resource_id}")
    prefix, b62 = m.group(1), m.group(2)
    asset_type = _PREFIX_TO_TYPE.get(prefix)
    if not asset_type:
        raise ValueError(f"Unknown type prefix: {prefix}")
    raw = _base62_to_bytes(b62, 12)
    return asset_type, raw.hex()


def parse_uri(uri: str) -> dict:
    """Parse a resource URI into components.

    Supports:
      - Resource ID: "v-BV1bW411n7fY9x01"
      - BSS URI: "bss://host:port/videos/id"
      - File URI: "file:///path/to/file.mp4"
      - S3 URI: "s3://bucket/key"
    """
    if _RESOURCE_ID_RE.match(uri):
        asset_type, object_id = decode_resource_id(uri)
        return {"scheme": "resource", "type": asset_type, "id": object_id}

    if uri.startswith("bss://"):
        rest = uri[6:]  # strip "bss://"
        # bss://host:port/videos/id
        parts = rest.split("/", 1)
        host = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        # Extract video ID from path like "videos/69e7264a"
        segments = path.split("/")
        return {
            "scheme": "bss",
            "host": host,
            "bss_url": f"http://{host}",
            "path": path,
            "id": segments[-1] if segments else "",
        }

    if uri.startswith("file://"):
        return {"scheme": "file", "path": uri[7:]}

    if uri.startswith("s3://"):
        rest = uri[5:]
        bucket, _, key = rest.partition("/")
        return {"scheme": "s3", "bucket": bucket, "key": key}

    raise ValueError(f"Unknown URI scheme: {uri}")
