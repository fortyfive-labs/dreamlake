"""Collaborative Notes reads and edits.

``read_snapshot()`` returns exact v2 source, content hash, and the original RTC
revision. ``patch(..., base_revision=snapshot.revision)`` validates that saved
source and maps changes to its original RTC identities through ordinary CRDT
synchronization. ``exact=True`` opts in to a revision check for one request; explicit
``if_match=`` remains an exact-mode compatibility alias.

Editable ``read()``/``Doc.save()`` drafts and whole-body edits use original
native snapshots by default. Explicit legacy drafts/body writes and section
writes retain ETag semantics. ``patch(..., legacy=True)`` explicitly
selects the previous unified-diff interface and its cached-ETag guard. Neither
path replaces an unavailable original baseline with a fresh read or retries an
ambiguous HTTP patch automatically.
"""

from __future__ import annotations

import re
import hashlib
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
    "NoteSnapshot",
    "SectionSnapshot",
    "PatchReceipt",
    "PatchResult",
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
    """The realtime service could not acknowledge the write (HTTP 409/503).

    Not "someone is editing" — a write normally goes INTO the live
    collaboration room and appears on their screens. This means the room was
    unreachable AND people are connected, so the only fallback (replacing the
    archive) would reset the room and cost them unsaved work.

    For a patch with an ambiguous acknowledgment, inspect current state before
    resubmitting; separate HTTP requests do not share an idempotency receipt.
    """


class NoteReadOnly(NoteError):
    """You may read this note but not write it (HTTP 403)."""


class PatchFailed(NoteError):
    """Patch source does not match its original baseline (HTTP 422).

    The draft and saved baseline remain available for inspection. A mismatch
    is never resolved by silently replacing the baseline with the current text.
    """


# ── Values ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NoteSnapshot:
    """Exact v2 source and its original RTC identity baseline."""

    note: str
    content: str
    hash: str
    revision: str

    def to_dict(self) -> dict[str, str]:
        return {"note": self.note, "content": self.content, "hash": self.hash, "revision": self.revision}


@dataclass(frozen=True)
class SectionSnapshot:
    """A backend-resolved source scope bound to a full original native snapshot."""
    anchor: str
    text: str
    start: int
    end: int
    baseline: NoteSnapshot


@dataclass(frozen=True)
class PatchReceipt:
    """V2 patch acknowledgment; content hash and RTC revision are distinct."""

    note: str
    mode: str
    base_revision: str
    hash: str
    revision: str

    def to_dict(self) -> dict[str, str]:
        """Use the same field names as the API and CLI JSON response."""
        return {"note": self.note, "mode": self.mode, "baseRevision": self.base_revision,
                "hash": self.hash, "revision": self.revision}


def _revision_token(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value == "*" or "\r" in value or "\n" in value:
        raise ValueError(f"{name} must be an original saved revision")
    return value


def _v2_tokens(data: Any, note_id: str) -> None:
    if (not isinstance(data, dict) or data.get("note") != note_id
            or not isinstance(data.get("hash"), str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", data["hash"])):
        raise NoteError("server did not return the Notes v2 contract; no legacy fallback was attempted")
    try:
        _revision_token(data.get("revision"), "server revision")
    except ValueError as error:
        raise NoteError(str(error)) from error


def _escape_inline(value: str) -> str:
    """Escape Notes inline framing and record control characters."""
    return "".join("\\" + c if c in "\\[]{}" else "\\n" if c == "\n" else
                   "\\r" if c == "\r" else "\\t" if c == "\t" else c for c in value)


class PatchResult(str):
    """What an explicit legacy patch committed.

    Subclasses `str` and equals its own ETag, so `note.patch(..., legacy=True)` can grow
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
    baseline: NoteSnapshot | None = None
    start: int | None = None
    end: int | None = None
    slice_hash: str | None = None

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
    ``patch(legacy=True, if_match=...)`` and a note edited in between is refused rather than
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
    if resp.status_code in (409, 503):
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
        self._text_snapshot: NoteSnapshot | None = None

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

    def refresh(self, *, legacy: bool = False) -> "Note":
        """Re-read the body. The thing to call after NoteChanged."""
        if legacy:
            data = self._get("/body")
            self._text = data["text"]
            self._etag = data.get("etag")
            self._text_snapshot = None
        else:
            original = self.read_snapshot()
            self._text = original.content
            self._text_snapshot = original
            self._etag = '"' + original.hash.removeprefix('sha256:') + '"'
        return self

    def diff(self, *, since: str | None = None, context: int = 3, legacy: bool = False) -> str:
        """Inspect a remote source diff without moving saved draft baselines.

        Modern inspection uses the shipped v2 body diff and verifies its
        acknowledged retained source. ``since`` is a content-hash reference,
        never an RTC write baseline. Explicit legacy retains context selection.
        """
        if isinstance(context, bool) or not isinstance(context, int) or not 0 <= context <= 100:
            raise ValueError("context must be an integer between 0 and 100")
        ref = self._etag if since is None else since
        if not isinstance(ref, str) or not ref:
            raise ValueError("read the note or refresh first, or supply since=<content hash>")
        if legacy:
            from urllib.parse import urlencode
            return self._get("/diff?" + urlencode({"since": ref, "context": context}))["diff"]
        match = re.fullmatch(r'(?:sha256:([a-f0-9]{64})|"([a-f0-9]{64})"|([a-f0-9]{64}))', ref)
        if not match:
            raise ValueError("since must be a content hash; RTC revisions are not diff references")
        base_hash = "sha256:" + next(value for value in match.groups() if value is not None)
        with self._client.http() as http:
            params = {"contract": "v2", "since": base_hash, "format": "diff"}
            if context != 3:
                params['context'] = str(context)
            response = http.get(f"{self._base}/body", params=params, follow_redirects=False)
        _raise_for(response, f"diff {self._ns}/{self._id}")
        if response.status_code != 200:
            raise NoteError("unexpected diff response; source and draft baseline were not adopted")
        try:
            data = response.json()
            _v2_tokens(data, self._id)
            if data.get("base") != base_hash or data.get("format") != "diff" or not isinstance(data.get("patch"), str):
                raise NoteError("inconsistent source diff")
            if len(data['patch'].encode('utf-8')) > 8_000_000:
                raise NoteError("source diff too large")
        except (ValueError, NoteError):
            raise NoteError("invalid source diff response; no cache or baseline was adopted") from None
        acknowledged = self.read_snapshot(at=data["revision"])
        if acknowledged.hash != data["hash"]:
            raise NoteError("source diff does not match its acknowledged source hash")
        return data["patch"]

    def get_text(self, *, legacy: bool = False) -> str:
        """Read cached full source; modern cache is a verified v2 snapshot.

        Explicit legacy selects the previous body read on cache miss.
        Refresh is explicit; cached source never moves an existing Doc baseline.
        """
        if self._text is None or (not legacy and getattr(self, "_text_snapshot", None) is None):
            self.refresh(legacy=legacy)
        assert self._text is not None
        return self._text

    @property
    def text(self) -> str:
        """Full source through verified v2 cache; use refresh() to read current."""
        return self.get_text()

    def _section_original(self, at):
        from ._character_plan import points
        original = at.baseline if isinstance(at, SectionSnapshot) else at
        if original is None:
            if at is not None: raise ValueError("saved section requires a full original snapshot")
            original = self.read_snapshot()
        if not isinstance(original, NoteSnapshot) or original.note != self._id:
            raise ValueError("at must retain this Note's original full snapshot")
        _revision_token(original.revision, "at.revision")
        points(original.content)
        if original.hash != "sha256:" + hashlib.sha256(original.content.encode()).hexdigest():
            raise ValueError("original source hash mismatch")
        if isinstance(at, SectionSnapshot):
            if not isinstance(at.text,str) or not isinstance(at.anchor,str) or not at.anchor:
                raise ValueError("invalid saved section snapshot")
            self._validate_section_range(original, {"start": at.start, "end": at.end}, at.text, nonempty=True)
        return original

    @staticmethod
    def _validate_section_range(original, value, text=None, *, nonempty=False):
        if (not isinstance(value, dict) or type(value.get("start")) is not int or
                type(value.get("end")) is not int or not 0 <= value['start'] <= value['end'] <= len(original.content)):
            raise NoteError("invalid retained section range")
        if nonempty and value['start']==value['end']:
            raise NoteError("named section range must be nonempty")
        if text is not None and (not isinstance(text, str) or original.content[value['start']:value['end']] != text):
            raise NoteError("retained section source mismatch")
        return value['start'], value['end']

    def _read_section_envelope(self, path, original):
        with self._client.http() as http:
            response = http.get(f"{self._base}{path}", params={"contract": "v2", "at": original.revision}, follow_redirects=False)
        _raise_for(response, "read retained section")
        if response.status_code != 200:
            raise NoteError("unexpected retained section response; no fallback")
        try:
            data = response.json()
            _v2_tokens(data, self._id)
            if data['revision'] != original.revision or data['hash'] != original.hash:
                raise NoteError("retained section baseline mismatch")
        except ValueError:
            raise NoteError("invalid retained section response") from None
        return data

    def sections(self, *, at: NoteSnapshot | None = None, legacy: bool = False) -> list[Section]:
        """Outline from retained original source; existing start/end stay UTF-16.

        Native endpoint is a pending backend capability. No legacy fallback.
        """
        if legacy:
            if at is not None: raise ValueError("at and legacy cannot be combined")
            data = self._get("/sections"); self._etag = data.get("etag")
            return [Section(anchor=x['anchor'],title=x.get('title',''),level=x.get('level',0),start=x.get('start',0),end=x.get('end',0)) for x in data.get('sections',[])]
        original = self._section_original(at)
        data = self._read_section_envelope('/sections', original)
        rows = data.get('sections')
        if not isinstance(rows,list) or len(rows)>10000: raise NoteError("invalid retained outline")
        result=[]; seen=set(); previous=-1
        for row in rows:
            if (not isinstance(row,dict) or not isinstance(row.get('anchor'),str) or not row['anchor'] or row['anchor'] in seen or
                    not isinstance(row.get('title'),str) or type(row.get('level')) is not int or not 0 <= row['level'] <= 6):
                raise NoteError("invalid retained outline metadata")
            start,end = self._validate_section_range(original,row.get('range'),nonempty=True)
            if start<previous: raise NoteError("unordered retained outline")
            previous=start;seen.add(row['anchor'])
            result.append(Section(anchor=row['anchor'],title=row['title'],level=row['level'],
                start=len(original.content[:start].encode('utf-16-le'))//2,end=len(original.content[:end].encode('utf-16-le'))//2))
        return result

    def read_section_snapshot(self, anchor: str, *, at: NoteSnapshot | SectionSnapshot | None = None) -> SectionSnapshot:
        if not isinstance(anchor,str) or not anchor: raise ValueError("anchor must be nonempty")
        original=self._section_original(at)
        if isinstance(at,SectionSnapshot):
            if at.anchor!=anchor: raise ValueError("saved section anchor mismatch")
            return at
        data=self._read_section_envelope('/sections/'+_seg(anchor),original)
        if data.get('anchor')!=anchor: raise NoteError("retained section anchor mismatch")
        if not isinstance(data.get('text'),str): raise NoteError("invalid retained section text")
        start,end=self._validate_section_range(original,data.get('range'),data['text'],nonempty=True)
        return SectionSnapshot(anchor,data['text'],start,end,original)

    def read_section(self, anchor: str, *, at: NoteSnapshot | SectionSnapshot | None = None, legacy: bool = False) -> str:
        if legacy:
            if at is not None: raise ValueError("at and legacy cannot be combined")
            data=self._get('/sections/'+_seg(anchor));self._etag=data.get('etag');return data['text']
        return self.read_section_snapshot(anchor,at=at).text

    def _assert_section_write_ready(self):
        if getattr(self,'_pending_section_receipt',None) is not None:
            error=NoteError("previous section write acknowledged; reconcile discovery before resubmit")
            error.receipt=self._pending_section_receipt;raise error

    def _submit_section_edits(self, original, edits, exact):
        self._assert_section_write_ready()
        if not edits and not exact:
            return PatchReceipt(self._id,'merge',original.revision,original.hash,original.revision)
        records=[]
        for edit in edits:
            body=('[-'+_escape_inline(edit['expectedText'])+'-]' if edit['expectedText'] else '')
            body+=('{+'+_escape_inline(edit['replacementText'])+'+}' if edit['replacementText'] else '')
            records.append(f"@@ chars {edit['start']}:{edit['end']} @@\n~ {body}\n")
        return self.patch(''.join(records),base_revision=original.revision,exact=exact)

    def write_section(self, anchor: str, text: str, *, at: NoteSnapshot | SectionSnapshot | None = None,
                      exact: bool = False, legacy: bool = False, force: bool = False) -> PatchReceipt | str:
        self._assert_section_write_ready()
        if legacy or force:
            if at is not None or exact: raise ValueError("at/exact cannot mix with legacy/force")
            return self._send('PUT','/sections/'+_seg(anchor),{'text':text},force)['etag']
        from ._character_plan import points,character_diff
        points(text)
        section=self.read_section_snapshot(anchor,at=at)
        replacement=text if section.end==len(section.baseline.content) or text.endswith('\n') else text+'\n'
        edits=character_diff(section.text,replacement,offset=section.start)
        return self._submit_section_edits(section.baseline,edits,exact)

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
        *, at: NoteSnapshot | None = None, legacy: bool = False,
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
        if not legacy:
            for value in (start_line,end_line):
                if value is not None and (type(value) is not int or value<1):
                    raise ValueError("line numbers must be positive integers")
            if start_line is not None and end_line is not None and end_line<start_line:
                raise ValueError("end_line precedes start_line")
            if at is not None and not isinstance(at,NoteSnapshot):
                raise ValueError("partial reads require a full original NoteSnapshot")
            original=self._section_original(at)
            total=original.content.count('\n') + (1 if original.content and not original.content.endswith('\n') else 0)
            if start_line is None and end_line is None:
                return LineRange(original.content,'"'+original.hash[7:]+'"',1,total,total,False,
                    baseline=original,start=0,end=len(original.content),slice_hash=original.hash)
            requested_start=1 if start_line is None else start_line
            if total==0 or requested_start>total:
                raise ValueError("requested start line is outside original source")
            params={"contract":"v2","at":original.revision}
            if start_line is not None:params['startLine']=str(start_line)
            if end_line is not None:params['endLine']=str(end_line)
            with self._client.http() as http:
                response=http.get(f"{self._base}/body",params=params,follow_redirects=False)
            _raise_for(response,"read retained partial source")
            if response.status_code!=200:raise NoteError("unexpected partial source response; no fallback")
            try:
                data=response.json();_v2_tokens(data,self._id)
                if data['revision']!=original.revision or data['hash']!=original.hash or data.get('scope')!='lines':
                    raise NoteError("partial source baseline mismatch")
                if not isinstance(data.get('text'),str):raise NoteError("invalid partial source text")
                start,end=self._validate_section_range(original,data.get('range'),data['text'])
                slice_hash='sha256:'+hashlib.sha256(data['text'].encode()).hexdigest()
                actual_end=total if end_line is None else min(end_line,total)
                if (data.get('sliceHash')!=slice_hash or any(type(data.get(k)) is not int for k in ('startLine','endLine','totalLines')) or
                    data['startLine']!=requested_start or data['endLine']!=actual_end or data['totalLines']!=total or
                    type(data.get('truncated')) is not bool or data['truncated']!=(actual_end<total)):
                    raise NoteError("partial source metadata mismatch")
                if (start and original.content[start-1]!='\n' or end<len(original.content) and (not end or original.content[end-1]!='\n') or
                    original.content[:start].count('\n')!=requested_start-1 or
                    data['text'].count('\n')+(1 if data['text'] and not data['text'].endswith('\n') else 0)!=actual_end-requested_start+1):
                    raise NoteError("partial source line boundaries mismatch")
            except ValueError:
                raise NoteError("invalid partial source response") from None
            return LineRange(data['text'],'"'+original.hash[7:]+'"',requested_start,actual_end,total,data['truncated'],
                baseline=original,start=start,end=end,slice_hash=slice_hash)
        if at is not None:raise ValueError("at and legacy cannot be combined")
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

    def read(self, *, at: str | None = None, if_match: str | None = None, legacy: bool = False) -> "Doc":
        """Read an editable draft retaining its original native identity snapshot.

        ``legacy=True`` or an explicit legacy ETag ``if_match`` preserves the
        prior cached ETag draft. ``at`` selects an original retained RTC source.
        """
        from ._doc import Doc
        if legacy or if_match is not None:
            if at is not None:
                raise ValueError("at requires modern read; omit legacy and if_match")
            self.refresh(legacy=True)
            if if_match is not None and self.etag != if_match:
                raise NoteChanged(f"note changed since legacy ETag {if_match}", etag=self.etag)
            return Doc(self, self._text or "", self.etag)
        original = self.read_snapshot(at=at)
        self._text, self._text_snapshot = original.content, original
        self._etag = '"' + original.hash.removeprefix('sha256:') + '"'
        return Doc(self, original.content, self._etag, snapshot=original)

    def write(self, text: str, *, at: NoteSnapshot | None = None, exact: bool = False,
              if_match: str | None = None, legacy: bool = False, force: bool = False) -> PatchReceipt | str:
        """Plan disjoint native edits against original source, without coarse fallback.

        Explicit legacy/force/ETag selects the previous whole-body PUT. Modern
        callers retain ``at``; absent it, one verified source snapshot is read.
        """
        if legacy or force or if_match is not None:
            if at is not None or exact:
                raise ValueError("at and exact require modern write; omit legacy, force and if_match")
            if if_match is not None and force:
                raise ValueError("if_match and force cannot be combined")
            return self._send("PUT", "/body", {"text": text}, force, if_match)["etag"]
        from ._character_plan import character_diff, points
        points(text)  # Reject invalid source before any transport.
        if at is not None:
            if not isinstance(at, NoteSnapshot) or at.note != self._id or not isinstance(at.content, str):
                raise ValueError("at must be this Note's original NoteSnapshot")
            _revision_token(at.revision, "at.revision")
            points(at.content)
            if at.hash != "sha256:" + hashlib.sha256(at.content.encode()).hexdigest():
                raise ValueError("at source does not match its content hash")
        original = at if at is not None else self.read_snapshot()
        edits = character_diff(original.content, text)
        if not edits and not exact:
            return PatchReceipt(self._id, "merge", original.revision, original.hash, original.revision)
        records = []
        for edit in edits:
            body = ("[-" + _escape_inline(edit['expectedText']) + "-]" if edit['expectedText'] else "")
            body += ("{+" + _escape_inline(edit['replacementText']) + "+}" if edit['replacementText'] else "")
            records.append(f"@@ chars {edit['start']}:{edit['end']} @@\n~ {body}\n")
        return self.patch("".join(records), base_revision=original.revision, exact=exact)

    def read_snapshot(self, *, at: str | None = None, if_match: str | None = None) -> NoteSnapshot:
        """Read exact v2 source and an original RTC baseline, without editing a Doc.

        ``at`` reads retained source at its original revision and verifies it.
        It cannot be combined with ``if_match``. The legacy text/ETag cache is
        unchanged. ``if_match`` optionally verifies
        this read against one explicit RTC revision; it is never filled from
        the cached legacy ETag.
        """
        if at is not None:
            _revision_token(at, "at")
            if if_match is not None:
                raise ValueError("at selects retained source; if_match checks current source; give one")
        if if_match is not None:
            _revision_token(if_match, "if_match")
        with self._client.http() as http:
            response = http.get(f"{self._base}/body", params={"contract": "v2", **({"at": at} if at is not None else {})},
                                headers={} if if_match is None else {"If-Match": if_match},
                                follow_redirects=False)
        _raise_for(response, f"read {self._ns}/{self._id}")
        if response.status_code != 200:
            raise NoteError("unexpected snapshot response; original source was not adopted")
        try:
            data = response.json()
            _v2_tokens(data, self._id)
            if not isinstance(data.get("content"), str) or data["hash"] != "sha256:" + hashlib.sha256(data["content"].encode("utf-8")).hexdigest():
                raise NoteError("source does not match the returned hash")
        except ValueError:
            raise NoteError("invalid snapshot source response; cache and baseline were not adopted") from None
        if at is not None and data["revision"] != at:
            raise NoteError("server did not return the requested retained RTC revision")
        if if_match is not None and data["revision"] != if_match:
            raise NoteChanged("note changed since the acknowledged RTC revision")
        return NoteSnapshot(note=self._id, content=data["content"], hash=data["hash"], revision=data["revision"])

    def patch(
        self, diff: str, *, base_revision: str | None = None,
        if_match: str | None = None, format: str | None = None,
        exact: bool = False, legacy: bool = False, force: bool = False,
    ) -> PatchReceipt | PatchResult:
        """Apply a patch against its original source and RTC identities.

        Default requests send ``base_revision`` and no ``If-Match`` header.
        ``exact=True`` requires the original revision to still be current.
        Explicit ``if_match`` also selects exact mode and can supply the
        baseline when used alone. Both tokens must match when provided.
        ``format`` is ``inline-dff`` (default) or ``diff``. A successful v2
        response is a structured ``PatchReceipt`` matching CLI/API JSON.

        ``legacy=True`` preserves the prior unified-diff/cached-ETag interface
        and its string-compatible ``PatchResult``. Local ``Doc`` editing and
        save semantics are unchanged. Errors leave local drafts and caches
        untouched; an ambiguous outcome requires inspection before resubmission.
        """
        if legacy:
            if base_revision is not None or format is not None or exact:
                raise ValueError("base_revision, format and exact require the v2 patch contract")
            if if_match is not None and force:
                raise ValueError("if_match names a revision and force says to ignore one; give one")
            data = self._send("PATCH", "/body", {"diff": diff}, force, if_match)
            return PatchResult(etag=data["etag"], size_bytes=data.get("sizeBytes", 0))
        if force:
            raise ValueError("v2 patch does not need force; omit exact and if_match for a merge patch")
        baseline = _revision_token(base_revision if base_revision is not None else if_match, "base_revision")
        if if_match is not None:
            _revision_token(if_match, "if_match")
            if baseline != if_match:
                raise ValueError("base_revision and if_match must identify the same original snapshot")
        selected_format = "inline-dff" if format is None else format
        if selected_format not in ("inline-dff", "diff"):
            raise ValueError("format must be inline-dff or diff")
        if not isinstance(diff, str) or len(diff.encode("utf-8")) > 8_000_000:
            raise ValueError("patch must be a UTF-8 string of at most 8000000 bytes")
        mode = "exact" if exact or if_match is not None else "merge"
        unknown = "write outcome unknown; keep original baseline/draft, reconcile before resubmit"
        try:
            with self._client.http() as http:
                response = http.request("PATCH", f"{self._base}/body",
                    json={"format": selected_format, "patch": diff, "baseRevision": baseline, "mode": mode},
                    headers={} if if_match is None else {"If-Match": if_match}, follow_redirects=False)
        except httpx.TransportError:
            raise NoteError(unknown) from None
        if response.status_code != 200:
            if response.status_code in (400, 401, 403, 404, 409, 412, 413, 415, 422, 503):
                _raise_for(response, f"patch {self._ns}/{self._id}")
            raise NoteError(unknown)
        try:
            data = response.json()
            _v2_tokens(data, self._id)
            if data.get("mode") != mode or data.get("baseRevision") != baseline:
                raise NoteError("inconsistent receipt")
        except (ValueError, NoteError):
            raise NoteError(unknown) from None
        receipt = PatchReceipt(note=self._id, mode=mode, base_revision=baseline,
                               hash=data["hash"], revision=data["revision"])
        # Keep the legacy cache in its content-hash domain. Never put an RTC
        # revision into the ETag used by legacy Doc.save()/whole-body writes.
        self._etag = '"' + data["hash"].removeprefix("sha256:") + '"'
        self._text = None
        return receipt

    def insert_section(
        self,
        text: str,
        *,
        before: str | None = None,
        after: str | None = None,
        at: NoteSnapshot | SectionSnapshot | None = None,
        exact: bool = False, legacy: bool = False, force: bool = False,
    ) -> list[Section]:
        """Add a section, and return the note's new outline.

        `after` places it past that section AND its subsections — anything else
        would drop the new one inside the section you named. With neither
        `before` nor `after`, it goes at the end.

        The heading is part of `text`, so you choose its level; a `###` can go
        under a `##`. The outline comes back because the new anchor is only
        knowable afterwards — a duplicate title takes the next free suffix.
        """
        self._assert_section_write_ready()
        both = bool(before and after) if legacy or force else before is not None and after is not None
        if both:
            raise ValueError("pass before or after, not both")
        if not legacy and not force:
            for anchor_option in (before, after):
                if anchor_option is not None and (not isinstance(anchor_option,str) or not anchor_option):
                    raise ValueError("section placement anchor must be nonempty")
            from ._character_plan import points
            points(text)
            original=self._section_original(at)
            anchor=before if before is not None else after
            if anchor is not None:
                scope=self.read_section_snapshot(anchor,at=at if at is not None else original)
                cut=scope.start if before is not None else scope.end
            else: cut=len(original.content)
            body=text if text.endswith('\n') else text+'\n'
            lead='\n' if cut and original.content[cut-1]!='\n' else ''
            receipt=self._submit_section_edits(original,[dict(start=cut,end=cut,expectedText='',replacementText=lead+body)],exact)
            self._pending_section_receipt=receipt
            try:
                acknowledged=self.read_snapshot(at=receipt.revision)
                if acknowledged.hash!=receipt.hash: raise NoteError("acknowledged section hash mismatch")
                outline=self.sections(at=acknowledged)
            except Exception:
                error=NoteError("section write acknowledged but outline discovery failed; keep original source/draft and reconcile before resubmit")
                error.receipt=receipt;raise error from None
            self._pending_section_receipt=None
            return outline
        if at is not None or exact: raise ValueError("at/exact cannot mix with legacy/force")
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

    def delete_section(self, anchor: str, *, at: NoteSnapshot | SectionSnapshot | None = None,
                       exact: bool = False, legacy: bool = False, force: bool = False) -> PatchReceipt | str:
        """Remove a section and everything under it.

        The subtree goes because that is what the section is; leaving its
        subsections behind would promote them into the previous one.
        """
        self._assert_section_write_ready()
        if not legacy and not force:
            section=self.read_section_snapshot(anchor,at=at)
            edits=[] if not section.text else [dict(start=section.start,end=section.end,expectedText=section.text,replacementText='')]
            return self._submit_section_edits(section.baseline,edits,exact)
        if at is not None or exact: raise ValueError("at/exact cannot mix with legacy/force")
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

    def append(
        self, text: str, *, at: NoteSnapshot | None = None, exact: bool = False,
        legacy: bool = False, force: bool = False,
    ) -> PatchReceipt | str:
        """Append against original RTC source; return its acknowledged receipt.

        Supply ``at`` to retain a previously read snapshot without fetching a
        newer baseline. ``legacy=True`` (or compatible ``force=True``) retains
        cached text, ETag PUT and string return semantics. An empty addition
        preserves the LF separator behavior; an unchanged MERGE returns an
        observational receipt without writing. EXACT no-ops go through PATCH
        so the server checks their original precondition.
        """
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if legacy or force:
            if at is not None or exact:
                raise ValueError("at and exact require modern append; omit legacy and force")
            if self._text is None:
                self.refresh(legacy=True)
            current = self._text or ""
            if current and not current.endswith("\n"):
                current += "\n"
            return self.write(current + text, legacy=True, force=force)
        if at is not None:
            if not isinstance(at, NoteSnapshot) or at.note != self._id or not isinstance(at.content, str):
                raise ValueError("at must be this Note's original NoteSnapshot")
            _revision_token(at.revision, "at.revision")
            if at.hash != "sha256:" + hashlib.sha256(at.content.encode("utf-8")).hexdigest():
                raise ValueError("at source does not match its content hash")
        original = at if at is not None else self.read_snapshot()
        suffix = ("\n" if original.content and not original.content.endswith("\n") else "") + text
        if not suffix and not exact:
            return PatchReceipt(note=self._id, mode="merge", base_revision=original.revision,
                                hash=original.hash, revision=original.revision)
        escaped = _escape_inline(suffix)
        patch = f"@@ chars {len(original.content)}:{len(original.content)} @@\n~ {{+{escaped}+}}\n" if suffix else ""
        return self.patch(patch, base_revision=original.revision, exact=exact)


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
