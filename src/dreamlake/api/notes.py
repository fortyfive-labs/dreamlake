"""
Notes — programmatic reading and writing of collaborative documents.

Built for callers that read, think for a while, and write back: scripts, and
coding agents driving this through bash or a tool call. The gap between the
read and the write is where collaborative documents get destroyed, so the
concurrency control is not optional here — it is on by default.

    import dreamlake as dl

    note = dl.note("<namespace>/design-doc")
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
from typing import TYPE_CHECKING, Any, Iterator, Sequence

import httpx

from ._client import DreamLakeClient, get_client

if TYPE_CHECKING:  # pragma: no cover
    # Both are quoted in return annotations and imported inside the methods
    # that build them, to keep the import cycle broken at runtime. The block was
    # empty, so a type checker had nothing to resolve those annotations against
    # and reported `Doc` and `NoteFiles` as undefined names.
    from ._doc import Doc
    from ._files import NoteFiles

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


class PatchResult(str):
    """What a patch committed.

    Subclasses `str` and equals its own ETag, so `note.patch(...)` can grow
    fields without breaking a caller that treated the old return value as the
    revision string — comparisons, formatting and `if_match=` all still work.
    """

    # Annotated as well as slotted. `__slots__` reserves the storage; without
    # the annotation a type checker does not know the attribute exists and
    # flags every read of it.
    _size_bytes: int

    __slots__ = ("_size_bytes",)

    def __new__(cls, etag: str, size_bytes: int = 0) -> "PatchResult":
        self = super().__new__(cls, etag)
        self._size_bytes = size_bytes
        return self

    @property
    def etag(self) -> str:
        return str(self)

    @property
    def size_bytes(self) -> int:
        return self._size_bytes

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"PatchResult(etag={str(self)!r}, size_bytes={self.size_bytes})"


@dataclass(frozen=True)
class LineRange:
    """Part of a body, with enough context to know it is a part.

    `total_lines` and `truncated` are not decoration: without them a caller
    cannot tell "this is the whole note" from "this is the first page", and a
    rewrite built on a partial read silently drops everything it never saw.
    """

    text: str
    #: The revision of the WHOLE note, not of this range.
    etag: str | None
    #: 1-based, inclusive.
    start_line: int
    #: The last line actually returned — not necessarily the one asked for.
    end_line: int
    total_lines: int
    truncated: bool

    def __repr__(self) -> str:  # pragma: no cover - display only
        more = ", truncated" if self.truncated else ""
        return f"LineRange(lines {self.start_line}-{self.end_line} of {self.total_lines}{more})"


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


@dataclass(frozen=True)
class Hit:
    """One match, and everywhere it is.

    Three coordinates because three things consume them, and converting
    between them by hand is where off-by-ones come from:

        line    1-based, what you read and what ``line=`` takes
        column  1-based code points into that line, so it lines up with ``rg -n``
        ind     0-based end-exclusive code points from the start of the
                document — what ``replace(ind=...)`` addresses

    ``etag`` is the revision these offsets were computed against. Pass it to
    ``patch(if_match=...)`` and a note edited in between is refused rather than
    overwritten at offsets that have since moved.
    """

    note_id: str
    note_slug: str
    note_name: str
    #: Revision the offsets belong to. Use it as the write's precondition.
    etag: str
    line: int
    column: int
    ind: tuple[int, int]
    #: The whole line, not just the matched text — enough to judge the hit.
    text: str
    match: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    #: Innermost section containing the hit, for addressing it by section.
    anchor: str
    namespace: str = ""

    def open(self, *, client: DreamLakeClient | None = None) -> "Note":
        """The note this hit is in."""
        return Note(self.note_id, namespace=self.namespace, client=client)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Hit {self.note_slug}:{self.line}:{self.column} {self.match!r}>"


@dataclass(frozen=True)
class GrepResult:
    """Hits, plus what was NOT looked at.

    Every field after ``hits`` exists to stop a caller mistaking a bounded
    answer for a complete one — which is how "replace all of them" quietly
    replaces some of them.
    """

    hits: tuple[Hit, ...]
    #: A note held more matches than the server returns for one note.
    truncated: bool
    #: How many notes the caller was allowed to see and search.
    notes_searched: int
    #: Notes too large to index in full; their tail was not searched.
    partial_notes: tuple[str, ...]
    #: Open notes whose unsaved text could not be flushed in time.
    stale_notes: int
    #: Opaque; pass back as ``cursor=`` for the next page. None at the end.
    next_cursor: str | None

    def __iter__(self):
        return iter(self.hits)

    def __len__(self) -> int:
        return len(self.hits)

    def __repr__(self) -> str:  # pragma: no cover - display only
        more = ", truncated" if self.truncated else ""
        return f"GrepResult({len(self.hits)} hits from {self.notes_searched} notes{more})"


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

    def _send(
        self,
        method: str,
        path: str,
        payload: dict,
        force: bool,
        if_match: str | None = None,
    ) -> dict:
        headers = {}
        # The default is a conditional write. Sending no validator would make
        # every edit a last-writer-wins overwrite, which for a document several
        # people share is data loss with a success code on it.
        #
        # An explicit `if_match` wins over the cached one: a caller who names a
        # revision is writing against THAT one, and quietly substituting a
        # newer one this object happens to hold would defeat the check.
        if if_match is not None:
            headers["If-Match"] = if_match
        elif not force and self._etag:
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

    def diff(self, *, since: str | None = None, context: int = 3) -> str:
        """Fetch a unified diff since a read/edit reference (the quoted ETag).

        Defaults to this handle's last read or successful write. Inspection
        does not advance the cached revision or the write precondition. Pass
        a saved ETag explicitly to compare across handles or sessions.
        """
        from urllib.parse import urlencode

        if isinstance(context, bool) or not isinstance(context, int) or not 0 <= context <= 100:
            raise ValueError("context must be an integer between 0 and 100")
        ref = self._etag if since is None else since
        if not ref:
            raise ValueError("read the note first or supply since=<etag>")
        query = urlencode({"since": ref, "context": context})
        return self._get(f"/diff?{query}")["diff"]

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

    @property
    def files(self) -> "NoteFiles":
        """Files attached to this note.

            note.files.list("assets/*.png")
            note.files.upload(Path("diagram.png"), path="assets/diagram.png")

        Distinct from note MEDIA, which is the image-embedding path: a media
        URL is its own credential and readable by anyone holding it, which is
        right for a picture in a public note and wrong for an attachment. These
        inherit the note's permissions on every read.
        """
        from ._files import NoteFiles

        return NoteFiles(self)

    def read_lines(
        self,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> "LineRange":
        """Read part of the body, and say what was left out.

            part = note.read_lines(1, 40)
            part.truncated      # there is more below
            part.total_lines    # how much more

        For looking at a large note without pulling all of it. NOT a basis for
        a write: the ETag identifies the WHOLE snapshot, so sending `part.text`
        back as the body would delete everything outside the range. Use
        `read()` and edit the draft, or address the range with `line=`.
        """
        params = {}
        if start_line is not None:
            params["startLine"] = str(start_line)
        if end_line is not None:
            params["endLine"] = str(end_line)
        with self._client.http() as http:
            r = http.get(f"{self._base}/body", params=params)
        _raise_for(r, f"read {self._ns}/{self._id}")
        d = r.json()
        return LineRange(
            text=d["text"],
            etag=d.get("etag"),
            start_line=d.get("startLine", 1),
            end_line=d.get("endLine", 1),
            total_lines=d.get("totalLines", 1),
            truncated=bool(d.get("truncated", False)),
        )

    def read(self, *, if_match: str | None = None) -> "Doc":
        """Load the source and its revision as an editable snapshot.

            doc = dl.note("<uuid>").read()
            doc.replace("Published", query="Draft", all=True)
            doc.save()

        `if_match` makes this a VERIFICATION read: the note must still be at
        that revision, and `NoteChanged` is raised if it is not. The use is
        read-after-write —

            rev = note.patch(diff, if_match=doc.etag)
            check = note.read(if_match=rev.etag)   # the version just committed

        — where returning whatever happens to be there now would quietly
        validate a different snapshot than the one being checked, and report
        success for someone else's document.

        A separate entry point from `text`, which returns a plain string and
        has callers — changing what that returns to hand back an object would
        break them silently, since a string and a Doc both print.
        """
        from ._doc import Doc

        self.refresh()
        if if_match is not None and self.etag != if_match:
            raise NoteChanged(
                f"note is at {self.etag}, not {if_match} — it changed since that revision",
                etag=self.etag,
            )
        return Doc(self, self._text or "", self.etag)

    def write(self, text: str, *, if_match: str | None = None, force: bool = False) -> str:
        """Replace the whole body.

        The counterpart of `write_section`, which replaces one part of it.

        `if_match` names the revision to write against. Without it the note's
        own cached revision is used — which is right for a caller that only
        ever writes through this object, and wrong for one holding a snapshot
        taken earlier: the cache moves on every write, so a draft read before
        one of them would be checked against a revision it never saw.
        """
        return self._send("PUT", "/body", {"text": text}, force, if_match)["etag"]

    def patch(
        self, diff: str, *, if_match: str | None = None, force: bool = False
    ) -> "PatchResult":
        """Apply a unified diff. Returns the commit's `etag` and `size_bytes`.

        The result compares and prints as the ETag string, so code written
        against the older `str` return keeps working while `result.etag` — the
        spelling the documented recipes use — also resolves.

        Self-verifying in one sense: the diff's context is its own
        precondition, so a document that moved refuses the patch (PatchFailed)
        rather than taking half of it. That is NOT the same as knowing the
        source is current — a patch can apply cleanly after unrelated changes
        elsewhere in the file — so the revision is still checked.

        `if_match` names the revision to write against, for a caller holding
        one from an earlier read. Without it the note's own cached revision is
        used; `force` sends no precondition at all.
        """
        if if_match is not None and force:
            raise ValueError("if_match names a revision and force says to ignore one; give one")
        data = self._send("PATCH", "/body", {"diff": diff}, force, if_match)
        return PatchResult(etag=data["etag"], size_bytes=data.get("sizeBytes", 0))

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
    """``"<id>"``, ``"ns/slug"`` or ``"ns/<id>"`` -> ``(namespace, note_id)``.

    An id identifies a note on its own, so it does not need a namespace in
    front of it — the server resolves which namespace it is in, and answers
    404 for a note the caller may not see exactly as it does for one that does
    not exist. A slug is only unique within a namespace, so that form still
    names one.
    """
    if "/" not in ref:
        if not _OBJECT_ID.match(ref):
            raise ValueError(
                f"note reference {ref!r} is neither an id nor '<namespace>/<slug>' — "
                "an id is 24 hex characters"
            )
        with client.http() as http:
            r = http.get(f"/notes/{_seg(ref)}")
        _raise_for(r, f"resolve {ref}")
        row = r.json()
        return row["namespaceSlug"], row["id"]

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

        dl.note("<namespace>/design-doc")
        dl.note("<namespace>/507f1f77bcf86cd799439011")
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

        note = dl.create_note("<namespace>", "Design Doc", text="# Design Doc\n")

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


def grep_notes(
    query: str | None = None,
    *,
    namespace: str,
    regex: str | None = None,
    flags: str = "",
    case_sensitive: bool | None = None,
    glob: str | None = None,
    notes: Sequence[str] | None = None,
    context: int = 0,
    limit: int = 50,
    cursor: str | None = None,
    client: DreamLakeClient | None = None,
) -> GrepResult:
    """Search note bodies and get the location of every hit.

    ``search_notes`` answers "which notes contain this". This answers "where",
    so you can go straight to an edit:

        for hit in dl.grep_notes("TODO", namespace="me"):
            print(f"{hit.note_slug}:{hit.line}:{hit.column}  {hit.text}")

        hit = dl.grep_notes("Draft", namespace="me").hits[0]
        doc = hit.open().read()
        doc.replace("Published", ind=hit.ind)
        doc.save()

    Not the same thing as ``doc.find()``: that searches ONE note you have
    already loaded, locally and exactly. This searches every note in a
    namespace you may see, on the server, against the indexed copy.

    Literal and case-insensitive by default — punctuation in ``query`` matches
    itself, so searching for "v1.2" does not also find "v1x2". Pass ``regex=``
    for a pattern; that defaults to case-SENSITIVE, because a pattern is
    written deliberately. ``flags`` takes any of i, m, s, u; "g" is refused,
    since whether all matches are wanted is decided by this call.

    ``limit`` is a number of HITS, and pages are followed to reach it. Read
    ``.truncated`` and ``.partial_notes`` before concluding you have seen every
    occurrence.
    """
    c = client or get_client()
    params: dict[str, Any] = {"limit": min(limit, 200), "context": context}
    if query is not None:
        params["q"] = query
    if regex is not None:
        params["regex"] = regex
    if flags:
        params["flags"] = flags
    if case_sensitive is not None:
        params["caseSensitive"] = "true" if case_sensitive else "false"
    if glob:
        params["glob"] = glob
    if notes:
        params["note"] = ",".join(notes)

    hits: list[Hit] = []
    truncated = False
    searched = 0
    partial: list[str] = []
    stale = 0
    next_cursor = cursor

    # Follow pages until `limit` hits are in hand. A caller asking for 50 hits
    # means 50 hits, not "50 or fewer depending on how the server chose to
    # chunk them" — and a page boundary is not something they can see.
    while True:
        page_params = dict(params)
        page_params["limit"] = min(limit - len(hits), 200)
        if next_cursor:
            page_params["cursor"] = next_cursor
        with c.http() as http:
            r = http.get(f"/namespaces/{_seg(namespace)}/notes/search", params=page_params)
        _raise_for(r, f"search notes in {namespace}")
        d = r.json()

        for row in d.get("hits", []):
            ind = row.get("ind") or [0, 0]
            hits.append(
                Hit(
                    note_id=row.get("noteId", ""),
                    note_slug=row.get("noteSlug", ""),
                    note_name=row.get("noteName", ""),
                    etag=row.get("etag", ""),
                    line=row.get("line", 1),
                    column=row.get("column", 1),
                    ind=(ind[0], ind[1]),
                    text=row.get("text", ""),
                    match=row.get("match", ""),
                    before=tuple(row.get("before") or ()),
                    after=tuple(row.get("after") or ()),
                    anchor=row.get("anchor", ""),
                    namespace=namespace,
                )
            )
        truncated = truncated or bool(d.get("truncated"))
        searched += int(d.get("notesSearched") or 0)
        partial.extend(d.get("partialNotes") or ())
        stale = max(stale, int(d.get("staleNotes") or 0))
        next_cursor = d.get("nextCursor")

        if not next_cursor or len(hits) >= limit:
            break

    return GrepResult(
        hits=tuple(hits),
        truncated=truncated,
        notes_searched=searched,
        partial_notes=tuple(dict.fromkeys(partial)),
        stale_notes=stale,
        next_cursor=next_cursor,
    )


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
