"""
Notes — programmatic reading and writing of collaborative documents.

Built for callers that read, think for a while, and write back: scripts, and
coding agents driving this through bash or a tool call. The gap between the
read and the write is where collaborative documents get destroyed, so the
concurrency control is not optional here — it is on by default.

    import dreamlake as dl

    note = dl.note("charlie/design-doc")
    note.sections()                       # what is in it
    note.read("install")                  # one section
    note.write("install", "## Install\\n…") # replace that section, safely

Every write is a real-time collaborative edit: the server applies it inside
the note's collaboration room, so anyone with it open watches the change
appear, and it merges with their typing the way two people's edits merge.
Nothing is locked and nobody has to close the note first.

That settles two edits arriving at once. It does not settle an edit built
from a document that has since moved — which is the other half. Every read
records the note's ETag and every write sends it back; a note that changed in
between raises NoteChanged rather than flattening whoever changed it, and
`note.refresh()` gets you a current copy to redo the edit against. Pass
``force=True`` to write unconditionally, which is occasionally what you want
and never what you want by accident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

import httpx

from ._client import DreamLakeClient, get_client

if TYPE_CHECKING:  # pragma: no cover
    pass

__all__ = [
    "Note",
    "NoteRef",
    "Section",
    "SectionMatch",
    "NoteError",
    "NoteNotFound",
    "NoteChanged",
    "NoteBusy",
    "NoteReadOnly",
    "PatchFailed",
    "note",
    "search_notes",
    "list_notes",
    "create_note",
    "shared_with_me",
]

_OBJECT_ID = re.compile(r"^[a-f0-9]{24}$", re.I)


# ── Errors ───────────────────────────────────────────────────────────────────


class NoteError(RuntimeError):
    """Base for every failure this module raises deliberately."""


class NoteNotFound(NoteError):
    pass


class NoteChanged(NoteError):
    """The note moved between your read and your write (HTTP 412).

    Not a transient error: retrying the same write reproduces it. Call
    ``note.refresh()``, redo the edit against the new text, and write again.
    """

    def __init__(self, message: str, etag: str | None = None):
        super().__init__(message)
        self.etag = etag


class NoteBusy(NoteError):
    """The realtime service could not take the write (HTTP 409).

    Not "someone is editing" — a write normally goes INTO the live
    collaboration room and appears on their screens. This means the room was
    unreachable AND people are connected, so the only fallback (replacing the
    archive) would reset the room and cost them unsaved work.

    Transient: an infrastructure signal, worth retrying after a pause.
    """


class NoteReadOnly(NoteError):
    """You may read this note but not write it (HTTP 403)."""


class PatchFailed(NoteError):
    """The diff did not apply to the current body (HTTP 422).

    The patch's own context is the precondition; a mismatch means the document
    moved under it. Re-read and regenerate the diff.
    """


# ── Values ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Section:
    """One addressable part of a note: a heading plus everything under it."""

    anchor: str
    title: str
    level: int
    start: int
    end: int

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Section {self.anchor!r} h{self.level} {self.title!r}>"


@dataclass(frozen=True)
class SectionMatch:
    """Where a search hit landed inside a note."""

    anchor: str
    title: str
    snippet: str

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<SectionMatch {self.anchor!r} {self.snippet[:40]!r}>"


@dataclass(frozen=True)
class NoteRef:
    """A note in a listing. Call ``.open()`` for one you can read and write."""

    id: str
    namespace: str
    name: str
    slug: str
    updated_at: str | None = None
    shared_by: str | None = None
    #: Which sections matched, on a search result. Empty otherwise.
    matches: tuple[SectionMatch, ...] = ()

    def open(self, *, client: DreamLakeClient | None = None) -> "Note":
        return Note(self.id, namespace=self.namespace, client=client)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<NoteRef {self.namespace}/{self.slug}>"


def _raise_for(resp: httpx.Response, what: str) -> None:
    """Turn a response into the error a caller can actually act on."""
    if resp.status_code < 400:
        return
    try:
        body = resp.json()
    except Exception:
        body = {}
    msg = body.get("message") or body.get("error") or resp.text or resp.reason_phrase
    if resp.status_code == 412:
        raise NoteChanged(f"{what}: {msg}", etag=body.get("etag") or resp.headers.get("etag"))
    if resp.status_code == 409:
        raise NoteBusy(f"{what}: {msg}")
    if resp.status_code == 422:
        raise PatchFailed(f"{what}: {msg}")
    if resp.status_code == 403:
        raise NoteReadOnly(f"{what}: {msg}")
    if resp.status_code == 404:
        raise NoteNotFound(f"{what}: {msg}")
    raise NoteError(f"{what}: HTTP {resp.status_code} {msg}")


# ── The note ─────────────────────────────────────────────────────────────────


class Note:
    """A note you can read and edit.

    Addressed as ``namespace/slug`` or ``namespace/<id>``; see ``dl.note()``,
    which resolves both. The text is fetched on first use and cached with the
    ETag it came with, so a write knows what it was based on.
    """

    def __init__(
        self,
        note_id: str,
        *,
        namespace: str,
        client: DreamLakeClient | None = None,
    ):
        self._id = note_id
        self._ns = namespace
        self._client = client or get_client()
        self._text: str | None = None
        self._etag: str | None = None

    # ── identity ────────────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        return self._id

    @property
    def namespace(self) -> str:
        return self._ns

    @property
    def etag(self) -> str | None:
        """The version this object last read. ``None`` until something is read."""
        return self._etag

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Note {self._ns}/{self._id}>"

    # ── plumbing ────────────────────────────────────────────────────────────

    @property
    def _base(self) -> str:
        return f"/namespaces/{_seg(self._ns)}/notes/{_seg(self._id)}"

    def _get(self, path: str) -> dict:
        with self._client.http() as http:
            r = http.get(f"{self._base}{path}")
        _raise_for(r, f"read {self._ns}/{self._id}")
        return r.json()

    def _send(self, method: str, path: str, payload: dict, force: bool) -> dict:
        headers = {}
        # The default is a conditional write. Sending no validator would make
        # every edit a last-writer-wins overwrite, which for a document several
        # people share is data loss with a success code on it.
        if not force and self._etag:
            headers["If-Match"] = self._etag
        with self._client.http() as http:
            r = http.request(method, f"{self._base}{path}", json=payload, headers=headers)
        _raise_for(r, f"write {self._ns}/{self._id}")
        out = r.json()
        # Adopt the version we just created, so consecutive edits work without
        # a re-read in between.
        self._etag = out.get("etag") or r.headers.get("etag")
        self._text = None
        return out

    # ── reading ─────────────────────────────────────────────────────────────

    def refresh(self) -> "Note":
        """Re-read the body. The thing to call after NoteChanged."""
        data = self._get("/body")
        self._text = data["text"]
        self._etag = data.get("etag")
        return self

    @property
    def text(self) -> str:
        """The whole body. Fetched once, then cached — call ``refresh()`` for a
        current copy."""
        if self._text is None:
            self.refresh()
        assert self._text is not None
        return self._text

    def sections(self) -> list[Section]:
        """The heading outline, as addressable sections.

        A section is a heading plus everything under it, up to the next heading
        of the same or a higher level — so ``## Install`` contains its own
        ``### macOS``. Text before the first heading is addressed as
        ``preamble``.
        """
        data = self._get("/sections")
        self._etag = data.get("etag")
        return [
            Section(
                anchor=s["anchor"],
                title=s.get("title", ""),
                level=s.get("level", 0),
                start=s.get("start", 0),
                end=s.get("end", 0),
            )
            for s in data.get("sections", [])
        ]

    def read_section(self, anchor: str) -> str:
        """One section's text, heading included.

        Named for what it reads. `note.text` is the whole note; every method
        with `_section` in its name works on one part of it, and every method
        without works on all of it. There is no third rule.
        """
        data = self._get(f"/sections/{_seg(anchor)}")
        self._etag = data.get("etag")
        return data["text"]

    # ── writing ─────────────────────────────────────────────────────────────

    def write_section(self, anchor: str, text: str, *, force: bool = False) -> str:
        """Replace one section. Returns the note's new ETag.

        The text is taken verbatim, heading and all — which is how a section is
        renamed. Omit the heading and the section stops being one.
        """
        return self._send("PUT", f"/sections/{_seg(anchor)}", {"text": text}, force)["etag"]

    def write(self, text: str, *, force: bool = False) -> str:
        """Replace the whole body.

        The counterpart of `write_section`, which replaces one part of it.
        """
        return self._send("PUT", "/body", {"text": text}, force)["etag"]

    def patch(self, diff: str, *, force: bool = False) -> str:
        """Apply a unified diff.

        Self-verifying: the diff's context is its own precondition, so a
        document that moved refuses the patch (PatchFailed) rather than taking
        half of it.
        """
        return self._send("PATCH", "/body", {"diff": diff}, force)["etag"]

    def insert_section(
        self,
        text: str,
        *,
        before: str | None = None,
        after: str | None = None,
        force: bool = False,
    ) -> list[Section]:
        """Add a section, and return the note's new outline.

        `after` places it past that section AND its subsections — anything else
        would drop the new one inside the section you named. With neither
        `before` nor `after`, it goes at the end.

        The heading is part of `text`, so you choose its level; a `###` can go
        under a `##`. The outline comes back because the new anchor is only
        knowable afterwards — a duplicate title takes the next free suffix.
        """
        if before and after:
            raise ValueError("pass before or after, not both")
        payload: dict[str, Any] = {"text": text}
        if before:
            payload["before"] = before
        elif after:
            payload["after"] = after
        out = self._send("POST", "/sections", payload, force)
        return [
            Section(
                anchor=s["anchor"],
                title=s.get("title", ""),
                level=s.get("level", 0),
                start=s.get("start", 0),
                end=s.get("end", 0),
            )
            for s in out.get("sections", [])
        ]

    def delete_section(self, anchor: str, *, force: bool = False) -> str:
        """Remove a section and everything under it.

        The subtree goes because that is what the section is; leaving its
        subsections behind would promote them into the previous one.
        """
        headers = {}
        if not force and self._etag:
            headers["If-Match"] = self._etag
        with self._client.http() as http:
            r = http.delete(f"{self._base}/sections/{_seg(anchor)}", headers=headers)
        _raise_for(r, f"delete section {anchor!r} of {self._ns}/{self._id}")
        out = r.json()
        self._etag = out.get("etag") or r.headers.get("etag")
        self._text = None
        return out["etag"]

    def append(self, text: str, *, force: bool = False) -> str:
        """Add to the end of the note.

        Reads first, so this is conditional like any other write — two agents
        appending at once will not silently lose one of the additions.
        """
        current = self.text
        if current and not current.endswith("\n"):
            current += "\n"
        return self.write(current + text, force=force)


def _seg(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


# ── Module-level entry points ────────────────────────────────────────────────


def _resolve(ref: str, client: DreamLakeClient) -> tuple[str, str]:
    """``"ns/slug"`` or ``"ns/<id>"`` -> ``(namespace, note_id)``."""
    if "/" not in ref:
        raise ValueError(
            f"note reference {ref!r} needs a namespace, e.g. 'charlie/{ref}' — "
            "a note id alone does not say whose namespace it is in"
        )
    ns, rest = ref.split("/", 1)
    if _OBJECT_ID.match(rest):
        return ns, rest
    # A slug. The listing is the only way to turn one into an id, and `q`
    # matches the NAME, which is not the slug — so filter locally rather than
    # trusting the server-side match to be the same thing.
    with client.http() as http:
        r = http.get(f"/namespaces/{_seg(ns)}/notes", params={"limit": 200})
    _raise_for(r, f"resolve {ref}")
    for row in r.json().get("notes", []):
        if row.get("slug") == rest or row.get("id") == rest:
            return ns, row["id"]
    raise NoteNotFound(f"no note {rest!r} in namespace {ns!r}")


def note(ref: str, *, client: DreamLakeClient | None = None) -> Note:
    """Open a note by ``namespace/slug`` or ``namespace/<id>``.

        dl.note("charlie/design-doc")
        dl.note("charlie/507f1f77bcf86cd799439011")
    """
    c = client or get_client()
    ns, note_id = _resolve(ref, c)
    return Note(note_id, namespace=ns, client=c)


def create_note(
    namespace: str,
    name: str,
    *,
    text: str = "",
    visibility: str = "private",
    client: DreamLakeClient | None = None,
) -> Note:
    """Create a note, optionally with a body, and return it ready to edit.

        note = dl.create_note("charlie", "Design Doc", text="# Design Doc\n")

    Two calls behind one: the note is created, then the body written. The write
    is unconditional — the note is one request old and there is nothing yet for
    anyone to have changed.

    Titles may repeat; the server appends a suffix to the SLUG to keep it
    unique, so `note.id` is what to hold on to rather than the name you passed.
    """
    c = client or get_client()
    with c.http() as http:
        r = http.post(
            f"/namespaces/{_seg(namespace)}/notes",
            json={"name": name, "visibility": visibility},
        )
    _raise_for(r, f"create note {name!r} in {namespace}")
    note = Note(r.json()["note"]["id"], namespace=namespace, client=c)
    if text:
        note.write(text, force=True)
    return note


def list_notes(
    namespace: str,
    *,
    limit: int = 50,
    offset: int = 0,
    client: DreamLakeClient | None = None,
) -> list[NoteRef]:
    """Every note in a namespace you can see.

    `namespace` is required and has no default. A personal namespace and an
    organization's are different places holding different notes, and guessing
    which one you meant would quietly answer for the wrong one. An
    organization's slug is its own — `dreamlake org list` in the CLI shows the
    ones you belong to.
    """
    c = client or get_client()
    with c.http() as http:
        r = http.get(
            f"/namespaces/{_seg(namespace)}/notes",
            params={"limit": limit, "offset": offset},
        )
    _raise_for(r, f"list notes in {namespace}")
    return [_ref(row, namespace) for row in r.json().get("notes", [])]


def search_notes(
    query: str,
    *,
    namespace: str,
    limit: int = 50,
    client: DreamLakeClient | None = None,
) -> list[NoteRef]:
    """Notes in one namespace whose title or body contains `query`.

    Case-insensitive.

    Substring matching, so a phrase inside a note finds it and a fragment of a
    word or an identifier works too. CJK is matched the same way as anything
    else.

    A note written before bodies were indexed matches on its title only, until
    someone edits it or an administrator runs the one-off backfill.

    Current: the search flushes any note open in that namespace before
    querying, so a sentence a colleague typed seconds ago is findable without
    waiting for anything.

    Each result carries `.matches`: which sections the query was found in, with
    a snippet. That is what saves reading a whole note to locate the part you
    were looking for — go straight to `note.read_section(match.anchor)`.

    Scoped to one namespace, like `list_notes` — searching "everywhere" is not
    offered, because an organization you belong to and your own namespace are
    separate collections and a merged result would hide which is which. Use
    `shared_with_me()` for notes other people sent you.
    """
    c = client or get_client()
    with c.http() as http:
        r = http.get(
            f"/namespaces/{_seg(namespace)}/notes",
            params={"q": query, "limit": limit},
        )
    _raise_for(r, f"search notes in {namespace}")
    return [_ref(row, namespace) for row in r.json().get("notes", [])]


def shared_with_me(*, client: DreamLakeClient | None = None) -> list[NoteRef]:
    """Notes other people shared with you, across namespaces."""
    c = client or get_client()
    with c.http() as http:
        r = http.get("/me/notes/shared")
    _raise_for(r, "list shared notes")
    out = []
    for row in r.json().get("notes", []):
        owner = (row.get("owner") or {}).get("name")
        out.append(_ref(row, row.get("namespaceSlug", ""), shared_by=owner))
    return out


def _ref(row: dict[str, Any], namespace: str, shared_by: str | None = None) -> NoteRef:
    return NoteRef(
        id=row["id"],
        namespace=row.get("namespaceSlug") or namespace,
        name=row.get("name", ""),
        slug=row.get("slug", ""),
        updated_at=row.get("updatedAt"),
        shared_by=shared_by,
        matches=tuple(
            SectionMatch(
                anchor=m.get("anchor", ""),
                title=m.get("title", ""),
                snippet=m.get("snippet", ""),
            )
            for m in row.get("matches") or ()
        ),
    )
