"""Portable .url files for DreamLake's ordinary file lists and previews."""
import re
from urllib.parse import urlsplit, urlunsplit

LINK_FILE_MAX_BYTES = 64 * 1024


def _url(value: str) -> str:
    if not isinstance(value, str) or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("Use an HTTP(S) URL without credentials")
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("Use an HTTP(S) URL without credentials")
        _ = parsed.port
        if any(char.isspace() for char in parsed.netloc):
            raise ValueError("Use an HTTP(S) URL without credentials")
    except (ValueError, TypeError) as error:
        raise ValueError("Use an HTTP(S) URL without credentials") from error
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, parsed.fragment))


def encode_link_file(url: str) -> str:
    """Return .url contents; upload them using the ordinary file APIs."""
    content = f"[InternetShortcut]\r\nURL={_url(url)}\r\n"
    if len(content.encode("utf-8")) > LINK_FILE_MAX_BYTES:
        raise ValueError("Link files are limited to 64 KB")
    return content


def parse_link_file(content: str) -> str:
    """Read one HTTP(S) target; never execute or fetch the saved URL."""
    if len(content.encode("utf-8")) > LINK_FILE_MAX_BYTES:
        raise ValueError("Link files are limited to 64 KB")
    section, urls = "", []
    for line in content.removeprefix("\ufeff").splitlines():
        value = line.strip()
        if not value or value.startswith((";", "#")):
            continue
        if value.startswith("[") and value.endswith("]"):
            section = value.lower()
        elif section == "[internetshortcut]" and re.match(r"url\s*=", value, re.I):
            urls.append(value.split("=", 1)[1].strip())
    if len(urls) != 1:
        raise ValueError("This link file must contain one InternetShortcut URL")
    return _url(urls[0])
