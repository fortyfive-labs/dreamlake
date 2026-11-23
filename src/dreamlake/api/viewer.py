"""`:viewer[...]` references: the 3D Preview of a bundle entry, library asset or scene.

Grammar parity with dreamlake-ai ``lib/preview/reference.ts`` and the CLI's
``src/viewer/reference.ts``::

    ns/bundle[@N]#entry          a bundle entry (preferred)
    library:ns/lib#asset         a library asset
    scene:ns/name[@N]            a scene version

``viewer_directive`` gives the Memo/Chat source that shows the live preview;
``viewer_layout_request`` gives the browser layout request (view kind
``asset-preview``) that opens the Preview panel, as ``dl viewer open`` applies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import quote

_NAMESPACE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}\Z")
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
_ASSET = re.compile(r"[^\s#\[\]{}\\]{1,200}\Z")
_VERSION = re.compile(r"[1-9][0-9]{0,9}\Z")
_MAX_VERSION = 2**31 - 1

Placement = Literal["tab", "left", "right", "above", "below"]


@dataclass(frozen=True)
class PreviewRef:
    """A parsed reference. ``version`` is None for the latest (bundle, scene)."""

    type: Literal["bundle", "library", "scene"]
    namespace: str
    name: str
    """The bundle, library or scene name."""
    item: Optional[str] = None
    """The bundle entry or library asset; None for a scene."""
    version: Optional[int] = None

    def __str__(self) -> str:
        return format_viewer_ref(self)


def _version(value: Optional[str]) -> Optional[int] | bool:
    if value is None:
        return None
    if not _VERSION.match(value) or int(value) > _MAX_VERSION:
        return False
    return int(value)


def parse_viewer_ref(text: str) -> Optional[PreviewRef]:
    """Parse a reference; None when it is not valid. ``@latest`` is not accepted."""
    if not isinstance(text, str):
        return None
    if text.startswith("library:"):
        m = re.fullmatch(r"library:([^/#\s]+)/([^/#\s]+)#(.+)", text)
        if not m or not (_NAMESPACE.match(m[1]) and _NAME.match(m[2]) and _ASSET.match(m[3])):
            return None
        return PreviewRef("library", m[1], m[2], m[3])
    if text.startswith("scene:"):
        m = re.fullmatch(r"scene:([^/@#\s]+)/([^/@#\s]+)(?:@([^/@#\s]+))?", text)
        if not m or not (_NAMESPACE.match(m[1]) and _NAME.match(m[2])):
            return None
        v = _version(m[3])
        return None if v is False else PreviewRef("scene", m[1], m[2], None, v)  # type: ignore[arg-type]
    m = re.fullmatch(r"([^/@#\s]+)/([^/@#\s]+)(?:@([^/@#\s]+))?#([^/@#\s]+)", text)
    if not m or not (_NAMESPACE.match(m[1]) and _NAME.match(m[2]) and _NAME.match(m[4])):
        return None
    v = _version(m[3])
    return None if v is False else PreviewRef("bundle", m[1], m[2], m[4], v)  # type: ignore[arg-type]


def format_viewer_ref(ref: PreviewRef) -> str:
    """The canonical reference text."""
    if ref.type == "library":
        return f"library:{ref.namespace}/{ref.name}#{ref.item}"
    if ref.type == "scene":
        return f"scene:{ref.namespace}/{ref.name}" + (f"@{ref.version}" if ref.version else "")
    return f"{ref.namespace}/{ref.name}" + (f"@{ref.version}" if ref.version else "") + f"#{ref.item}"


def _checked(ref: PreviewRef | str) -> PreviewRef:
    parsed = parse_viewer_ref(ref) if isinstance(ref, str) else parse_viewer_ref(format_viewer_ref(ref))
    if parsed is None:
        raise ValueError(
            "Use a preview reference: ns/bundle[@N]#entry, library:ns/lib#asset or scene:ns/name[@N]"
        )
    return parsed


def viewer_directive(ref: PreviewRef | str) -> str:
    """The Memo/Chat directive that shows the 3D Preview, ``:viewer[...]``."""
    return f":viewer[{format_viewer_ref(_checked(ref))}]"


def viewer_href(ref: PreviewRef | str) -> str:
    """The resource's app page, relative to the web origin."""
    r = _checked(ref)
    q = lambda s: quote(s, safe="")  # noqa: E731
    if r.type == "library":
        return f"/{q(r.namespace)}/libraries/{q(r.name)}?asset={q(r.item or '')}"
    if r.type == "scene":
        return f"/{q(r.namespace)}/scenes/{q(r.name)}" + (f"?version={r.version}" if r.version else "")
    v = f"/v/{r.version}" if r.version else ""
    return f"/{q(r.namespace)}/bundles/{q(r.name)}{v}/entries/{q(r.item or '')}"


def viewer_layout_request(
    ref: PreviewRef | str, destination: str, if_revision: int, placement: Placement = "right"
) -> dict:
    """The revision-guarded layout request that opens the Preview panel.

    Only the reference travels: the browser resolves it as the signed-in user;
    no credentials or share tokens belong in the request.
    """
    if placement not in ("tab", "left", "right", "above", "below"):
        raise ValueError("placement must be tab, left, right, above or below")
    if not isinstance(if_revision, int) or if_revision < 0:
        raise ValueError("if_revision must be a non-negative integer")
    text = format_viewer_ref(_checked(ref))
    return {
        "action": "open",
        "view": {"kind": "asset-preview", "resource": text, "content": {"ref": text}},
        "destination": {"id": destination},
        "placement": placement,
        "ifRevision": if_revision,
    }


__all__ = [
    "PreviewRef",
    "parse_viewer_ref",
    "format_viewer_ref",
    "viewer_directive",
    "viewer_href",
    "viewer_layout_request",
]
