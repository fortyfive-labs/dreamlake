"""The Notes mapped snapshot: ``GET …/body?contract=v2&view=html&contentFormat=literal-markdown``.

The server answers with a document whose root ``<html>`` element carries the
identity (``data-note``, ``data-hash``, ``data-revision``), the exact selected
source (``data-source``), the offset unit and, for Markdown notes, the observed
addresses with their absolute code-point and line ranges (``data-addresses``).
A scoped read (section, address or outline) adds ``data-scope``,
``data-source-start``/``-end`` and ``data-source-hash``.

This is a port of the CLI's ``validateHtmlSnapshot`` and ``annotatedMarkdown``
(``src/cli/notes/html-read.ts``) and of the server's reading-view annotation
(``noteReadingPage``). Nothing here re-serializes HTML or DOM-parses the body:
the root attributes are the contract, the body is presentation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

__all__ = ["HtmlSnapshot", "HtmlSnapshotError", "annotated_markdown", "parse_html_snapshot"]

_ROOT = re.compile(r"^<!doctype html>\s*<html\b([^>]*)>", re.IGNORECASE)
_END = re.compile(r"</html>\s*$", re.IGNORECASE)
_ATTR = re.compile(r'\s+([a-zA-Z][a-zA-Z0-9-]*)="([^"]*)"')
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#[xX][\da-fA-F]+);")
_BARE_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#[xX][\da-fA-F]+);)")
_SECTION_ID = re.compile(r"^s[1-9]\d*(?:\.[1-9]\d*)*$")
_BLOCK_TAG = re.compile(r"<(h[1-6]|p|ul|ol|li)\b([^>]*)>", re.IGNORECASE)
_HEADING = re.compile(r"<(h[1-6])\b([^>]*)>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]*>")
_HASH = re.compile(r"sha256:[a-f0-9]{64}")
_NAMED = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'"}


class HtmlSnapshotError(ValueError):
    """The response is not the Notes HTML snapshot contract; nothing was adopted."""


def _attribute_value(value: str) -> str:
    if _BARE_AMP.search(value):
        raise HtmlSnapshotError("unsupported HTML snapshot attribute entity")

    def decode(match: re.Match[str]) -> str:
        entity = match.group(0)
        named = _NAMED.get(entity.lower())
        if named is not None:
            return named
        hexadecimal = entity[:3].lower() == "&#x"
        point = int(entity[3 if hexadecimal else 2:-1], 16 if hexadecimal else 10)
        if point < 1 or point > 0x10FFFF or 0xD800 <= point <= 0xDFFF or 0x80 <= point <= 0x9F:
            raise HtmlSnapshotError("invalid HTML snapshot attribute character reference")
        return chr(point)

    return _ENTITY.sub(decode, value)


def _attributes(raw: str, *, strict: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    end = 0
    for match in _ATTR.finditer(raw):
        if strict and match.start() != end:
            raise HtmlSnapshotError("invalid HTML snapshot root attributes")
        name = match.group(1).lower()
        if strict and name in out:
            raise HtmlSnapshotError("duplicate HTML snapshot root attribute")
        out[name] = _attribute_value(match.group(2))
        end = match.end()
    if strict and raw[end:].strip():
        raise HtmlSnapshotError("invalid HTML snapshot root attributes")
    return out


@dataclass(frozen=True)
class HtmlSnapshot:
    """What the root element asserts, verified against the source it carries."""

    note: str
    hash: str
    revision: str
    source_type: str
    #: Exact source of the selected scope; the complete source for an article read.
    source: str
    #: Code-point range of ``source`` in the complete source.
    start: int
    end: int
    #: ``article`` | section id | ``tag`` | ``toc``.
    scope: str
    target_id: str | None
    section_id: str | None
    #: Observed addresses: ``id``, ``data-char`` ``a:b``, ``data-lines`` ``x:y``,
    #: optional ``data-section-id``; ``data-source`` on an outline read.
    addresses: tuple[dict[str, str], ...]


def parse_html_snapshot(html: str, note: str, *, section: str | None = None, tag: str | None = None,
                        toc: bool = False) -> HtmlSnapshot:
    """Validate the snapshot exactly as the CLI does, then expose its root facts."""
    root = _ROOT.match(html)
    if not root or not _END.search(html):
        raise HtmlSnapshotError("server did not return a complete Notes HTML snapshot")
    attributes = _attributes(root.group(1), strict=True)
    revision, hash_, source = attributes.get("data-revision"), attributes.get("data-hash"), attributes.get("data-source")
    if (attributes.get("data-note") != note or not revision or "\r" in revision or "\n" in revision
            or not _HASH.fullmatch(hash_ or "") or source is None
            or attributes.get("data-offset-unit") != "unicode-code-point"
            or attributes.get("data-source-type") not in ("markdown", "html")):
        raise HtmlSnapshotError("server did not return the Notes HTML metadata contract; upgrade the server")
    assert hash_ is not None and revision is not None
    returned_scope = attributes.get("data-scope")
    source_hash: str | None = hash_
    start, end = 0, len(source)
    if section or toc or tag:
        wrong = (not returned_scope
                 or (returned_scope != "tag" or attributes.get("data-target-id") != tag) if tag
                 else returned_scope != "toc" if toc
                 else not _SECTION_ID.match(returned_scope))
        if not returned_scope or wrong:
            raise HtmlSnapshotError("server did not return the requested HTML scope")
        if section and _SECTION_ID.match(section) and returned_scope != section:
            raise HtmlSnapshotError("server returned a different section")
        if section and section != returned_scope and section != attributes.get("data-section-id"):
            raise HtmlSnapshotError("server returned a different section")
        raw_start, raw_end = attributes.get("data-source-start", ""), attributes.get("data-source-end", "")
        if not raw_start.isdigit() or not raw_end.isdigit() or int(raw_end) - int(raw_start) != len(source):
            raise HtmlSnapshotError("invalid scoped source range")
        if toc and (source != "" or raw_start != "0" or raw_end != "0"):
            raise HtmlSnapshotError("invalid TOC source")
        start, end = int(raw_start), int(raw_end)
        source_hash = attributes.get("data-source-hash")
    elif returned_scope and returned_scope != "article":
        raise HtmlSnapshotError("server returned partial HTML for a full read")
    if "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest() != source_hash:
        raise HtmlSnapshotError("HTML canonical source does not match its returned hash")
    if attributes.get("data-content-format") == "literal-markdown":
        try:
            raw_addresses = json.loads(attributes.get("data-addresses") or "[]")
        except ValueError:
            raise HtmlSnapshotError("invalid HTML snapshot addresses") from None
        if not isinstance(raw_addresses, list) or not all(isinstance(a, dict) for a in raw_addresses):
            raise HtmlSnapshotError("invalid HTML snapshot addresses")
        addresses = tuple({str(k): str(v) for k, v in a.items()} for a in raw_addresses)
    else:
        # An HTML-sourced note has no literal-Markdown address table; the body's
        # block elements carry the same id/data-char/data-lines attributes. The
        # tag is kept (heading level) and a heading's text stands in for its source.
        texts = {m.group(2): _TAGS.sub("", m.group(3)) for m in _HEADING.finditer(html)}
        found = []
        for match in _BLOCK_TAG.finditer(html):
            attributes_ = _attributes(match.group(2), strict=False)
            if "id" in attributes_ and "data-char" in attributes_:
                attributes_["tag"] = match.group(1).lower()
                if match.group(1).lower().startswith("h") and match.group(2) in texts:
                    attributes_.setdefault("data-source", texts[match.group(2)])
                found.append(attributes_)
        addresses = tuple(found)
    return HtmlSnapshot(note=note, hash=hash_, revision=revision, source_type=attributes["data-source-type"],
                        source=source, start=start, end=end, scope=returned_scope or "article",
                        target_id=attributes.get("data-target-id"), section_id=attributes.get("data-section-id"),
                        addresses=addresses)


def _hint(address: dict[str, str]) -> str:
    section = f" section={address['data-section-id']}" if address.get("data-section-id") else ""
    return f"<!-- {address.get('id')} chars={address.get('data-char')} lines={address.get('data-lines')}{section} -->\n"


def annotated_markdown(revision: str, hash_: str, scope: str, source: str, start: int,
                       addresses: tuple[dict[str, str], ...], *, toc: bool = False) -> str:
    """The reading view: generated hints before each observed block. Never source.

    Same construction as the server's ``noteReadingPage`` and the CLI's
    ``annotatedMarkdown``, so the three agree byte for byte on one snapshot.
    """
    header = f"<!-- notes-reading-view revision={revision} hash={hash_} scope={scope}; generated hints are not source -->\n"
    if toc:
        return header + "\n".join(_hint(a) + a.get("data-source", "") for a in addresses)
    cursor, out = 0, header
    for address in sorted(addresses, key=lambda a: int(a["data-char"].split(":")[0])):
        at = int(address["data-char"].split(":")[0]) - start
        if at < cursor or at > len(source):
            continue
        out += source[cursor:at] + _hint(address)
        cursor = at
    return out + source[cursor:]
