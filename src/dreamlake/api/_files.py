"""Files attached to a note.

    note = dl.note("<uuid>")
    note.files.create("examples/config.json", text='{"enabled": true}\\n')
    image = note.files.upload(Path("diagram.png"), path="assets/diagram.png")
    image.download(Path("copy.png"))

A file belongs to its note and inherits the note's permissions — unlike note
media, whose URL is its own credential. That is what lets one hold something
not meant to be world-readable.

Bytes are bytes here. Upload and download move `bytes`, never `str`, so a
round trip is exact for any content. The text helpers exist for text-typed
files and REFUSE anything else rather than decoding it: plenty of binaries
decode as UTF-8, and writing the result back corrupts them with no error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .notes import Note

__all__ = ["NoteFile", "NoteFiles", "FileExists", "NotTextFile", "NotPreviewable"]


class FileExists(Exception):
    """A path is already taken, and the call did not say to replace it."""


class NotPreviewable(Exception):
    """This file has no rendered form.

    Its own type because the remedy differs from every other refusal here:
    nothing is wrong with the file or the request, there is simply nothing to
    look at. Download it.
    """


class NotTextFile(Exception):
    """A text operation was asked of a file that is not text.

    Its own type because the remedy is specific: download the bytes. Reading
    them as a string would hand back something that looks fine and no longer
    round-trips.
    """


@dataclass(frozen=True)
class NoteFile:
    """One attached file, and the operations that act on it.

    `etag` is the sha256 of the content. Pass it as `if_match=` to a mutation
    and a file that changed since you read it is refused rather than
    overwritten.
    """

    id: str
    path: str
    content_type: str
    size_bytes: int
    etag: str
    created_by: str
    created_at: str
    updated_at: str
    #: In the trash. Still addressable by id; `restore()` brings it back.
    trashed: bool
    #: How a sandboxed preview would render it, or None for download-only.
    preview_kind: str | None
    #: Whether `text()` and text writes are allowed.
    is_text: bool

    _files: "NoteFiles | None" = None

    # ── reading ─────────────────────────────────────────────────────────────

    def bytes(self) -> bytes:
        """The file's exact content. Works for anything."""
        return self._owner().read_bytes(self.id)

    def text(self, encoding: str = "utf-8") -> str:
        """The content as a string. Refuses a file that is not text-typed."""
        if not self.is_text:
            raise NotTextFile(
                f"{self.path} is {self.content_type}; reading it as text would decode and "
                "re-encode the bytes, which corrupts them. Use .bytes() or .download()."
            )
        return self._owner().read_text(self.id)

    def download(self, dest: str | os.PathLike[str], *, overwrite: bool = False) -> Path:
        """Write the content to a local path. Returns the path written.

        Streamed to disk in chunks, so the file never has to fit in memory.

        `overwrite` defaults to False and raises on a collision. A download
        that silently replaces a local file is the one operation here that
        destroys something DreamLake cannot restore.
        """
        p = Path(dest)
        if p.is_dir():
            p = p / self.path.split("/")[-1]
        if p.exists() and not overwrite:
            raise FileExists(f"{p} already exists; pass overwrite=True to replace it")
        p.parent.mkdir(parents=True, exist_ok=True)

        # Written to a neighbouring temporary file and moved into place, so an
        # interrupted download leaves the destination untouched rather than
        # half a file that looks complete.
        tmp = p.with_name(p.name + ".part")
        try:
            with tmp.open("wb") as fh:
                for chunk in self._owner().iter_bytes(self.id):
                    fh.write(chunk)
            tmp.replace(p)
        finally:
            if tmp.exists():
                tmp.unlink()
        return p

    # ── changing ────────────────────────────────────────────────────────────

    def move(
        self,
        path: str,
        *,
        overwrite: bool = False,
        if_match: str | None = None,
    ) -> "NoteFile":
        """Rename or move. The id does not change, so held references survive."""
        return self._owner()._patch(
            self.id, {"path": path, "overwrite": overwrite}, if_match=if_match
        )

    def copy(self, path: str, *, overwrite: bool = False) -> "NoteFile":
        """Copy to another path. The copy gets its own storage, not a share."""
        return self._owner()._copy(self.id, path, overwrite=overwrite)

    def write(
        self,
        *,
        text: str | None = None,
        data: bytes | None = None,
        if_match: str | None = None,
    ) -> "NoteFile":
        """Replace this file's content, keeping its path and id."""
        return self._owner().put(
            self.path, text=text, data=data, content_type=self.content_type,
            overwrite=True, if_match=if_match,
        )

    def trash(self, *, if_match: str | None = None) -> "NoteFile":
        """Move to the trash. Recoverable with `restore()`."""
        self._owner()._delete(self.id, purge=False, if_match=if_match)
        return self._owner().get(self.id)

    def restore(self, *, path: str | None = None) -> "NoteFile":
        """Bring it back. Refused if its path has since been taken."""
        return self._owner()._restore(self.id, path)

    def purge(self, *, if_match: str | None = None) -> None:
        """Delete the stored bytes. Not recoverable — hence a separate call."""
        self._owner()._delete(self.id, purge=True, if_match=if_match)

    def preview_url(self, *, share: bool = False) -> str:
        """A link that renders this file.

            print(note.files.find("report.html").preview_url())

        The default needs you signed in: it opens a dashboard page that reads
        with your own login, so the note's permissions decide what it shows and
        the link grants nothing by itself.

        `share=True` returns a link that works WITHOUT signing in, for sending
        someone something they can look at. It does not expire — a link you
        hand over has to still work when they get round to opening it — and it
        keeps returning the same URL, so asking again never breaks a copy
        already given away. `unshare()` withdraws it, at once and for everyone.

        Minting one takes permission to share the note, not merely to read it:
        somebody who was given access must not be able to pass it on.

        Raises if the file has no rendered form; check `preview_kind` first, or
        download it instead.
        """
        return self._owner()._preview(self.id, share=share)["url"]

    def unshare(self) -> None:
        """Withdraw the shared preview link. Every copy stops working."""
        self._owner()._unshare(self.id)

    def refresh(self) -> "NoteFile":
        """Re-read the metadata."""
        return self._owner().get(self.id)

    def _owner(self) -> "NoteFiles":
        if self._files is None:
            raise RuntimeError("this NoteFile is detached from its note")
        return self._files

    def __repr__(self) -> str:  # pragma: no cover - display only
        where = " (trashed)" if self.trashed else ""
        return f"<NoteFile {self.path} {self.size_bytes}B {self.content_type}{where}>"


def _view(row: dict[str, Any], files: "NoteFiles") -> NoteFile:
    return NoteFile(
        id=row.get("id", ""),
        path=row.get("path", ""),
        content_type=row.get("contentType", ""),
        size_bytes=row.get("sizeBytes", 0),
        etag=row.get("etag", ""),
        created_by=row.get("createdBy", ""),
        created_at=row.get("createdAt", ""),
        updated_at=row.get("updatedAt", ""),
        trashed=bool(row.get("trashed", False)),
        preview_kind=row.get("previewKind"),
        is_text=bool(row.get("text", False)),
        _files=files,
    )


class NoteFiles:
    """`note.files` — the collection, reached from a Note."""

    __slots__ = ("_note",)

    def __init__(self, note: "Note") -> None:
        self._note = note

    # ── discovery ───────────────────────────────────────────────────────────

    def list(
        self,
        glob: str | None = None,
        *,
        content_type: str | None = None,
        trashed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NoteFile]:
        """Files, ordered by path.

            note.files.list("assets/*.png")
            note.files.list("**/*.json")

        `*` stops at a slash and `**` does not, the way every tool that walks a
        tree behaves. `trashed=True` lists the trash instead of the live files.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if glob:
            params["glob"] = glob
        if content_type:
            params["contentType"] = content_type
        if trashed:
            params["trashed"] = "true"
        data = self._get("", params=params)
        return [_view(row, self) for row in data.get("files", [])]

    def __iter__(self) -> Iterator[NoteFile]:
        return iter(self.list())

    def get(self, file_id: str) -> NoteFile:
        """One file's metadata, by id. Works for a trashed file too."""
        return _view(self._get(f"/{file_id}"), self)

    def find(self, path: str) -> NoteFile | None:
        """The live file at this path, or None."""
        for f in self.list():
            if f.path == path:
                return f
        return None

    # ── creating ────────────────────────────────────────────────────────────

    def create(
        self,
        path: str,
        *,
        text: str | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
        overwrite: bool = False,
    ) -> NoteFile:
        """Create a file from a string or from bytes.

        `overwrite` defaults to False and raises `FileExists` on a collision:
        a "create" that silently replaces is how a caller loses a file it did
        not know was there.
        """
        return self.put(
            path, text=text, data=data, content_type=content_type, overwrite=overwrite
        )

    def upload(
        self,
        source: str | os.PathLike[str],
        *,
        path: str | None = None,
        content_type: str | None = None,
        overwrite: bool = False,
    ) -> NoteFile:
        """Upload a local file. `path` defaults to its name.

            note.files.upload(Path("diagram.png"), path="assets/diagram.png")

        Read as bytes, so the upload is exact for any content.
        """
        src = Path(source)
        # Streamed from disk rather than read into memory. A 500 MB attachment
        # should cost a buffer, not half a gigabyte of resident memory — and
        # `read_bytes()` on something larger than RAM simply fails.
        with src.open("rb") as fh:
            return self.put(
                path or src.name,
                stream=fh,
                content_type=content_type,
                overwrite=overwrite,
            )

    def put(
        self,
        path: str,
        *,
        text: str | None = None,
        data: bytes | None = None,
        stream: Any | None = None,
        content_type: str | None = None,
        overwrite: bool = False,
        if_match: str | None = None,
    ) -> NoteFile:
        """Write a file, from text, bytes, or a binary file object.

        `stream` is any object with `.read()` opened in binary mode. It is
        handed to the transport as-is, so the file never has to fit in memory.
        """
        given = [x is not None for x in (text, data, stream)]
        if sum(given) != 1:
            raise ValueError("give exactly one of text, data or stream")

        headers = {"If-Match": if_match} if if_match else {}

        if text is not None:
            payload: dict[str, Any] = {"path": path, "text": text, "overwrite": overwrite}
            if content_type:
                payload["contentType"] = content_type
            with self._note._client.http() as http:
                r = http.post(self._base(), json=payload, headers=headers)
            return _view(self._checked(r, f"write {path}"), self)

        params: dict[str, Any] = {"path": path, "overwrite": "true" if overwrite else "false"}
        if content_type:
            params["contentType"] = content_type
        with self._note._client.http() as http:
            r = http.put(
                f"{self._base()}/content",
                params=params,
                # httpx streams a file object and sends bytes as they are;
                # either way nothing is decoded on the way out.
                content=data if data is not None else stream,
                headers={
                    # Declared so the server does not have to guess, and so the
                    # client never tries to encode the body as anything.
                    "Content-Type": content_type or "application/octet-stream",
                    **headers,
                },
            )
        return _view(self._checked(r, f"upload {path}"), self)

    # ── content ─────────────────────────────────────────────────────────────

    def iter_bytes(self, file_id: str, chunk_size: int = 1 << 20):
        """The content, a chunk at a time. Nothing is held whole in memory."""
        with self._note._client.http() as http:
            with http.stream(
                "GET",
                f"{self._base()}/{file_id}/content",
                params={"redirect": "false"},
            ) as r:
                if r.status_code >= 400:
                    r.read()
                    self._checked(r, f"download {file_id}", json=False)
                yield from r.iter_bytes(chunk_size)

    def read_bytes(self, file_id: str) -> bytes:
        """Exact content. `redirect=false` so the bytes come back here rather
        than as a 302 the caller would have to follow itself."""
        with self._note._client.http() as http:
            r = http.get(f"{self._base()}/{file_id}/content", params={"redirect": "false"})
        self._checked(r, f"download {file_id}", json=False)
        return r.content

    def read_text(self, file_id: str) -> str:
        with self._note._client.http() as http:
            r = http.get(f"{self._base()}/{file_id}/text")
        return self._checked(r, f"read {file_id}")["text"]

    # ── internals ───────────────────────────────────────────────────────────

    def _base(self) -> str:
        # The note's own base, not a second construction of it: escaping a
        # namespace slug twice, two different ways, is how one of them starts
        # addressing a different namespace.
        return f"{self._note._base}/files"

    def _get(self, suffix: str, params: dict[str, Any] | None = None) -> dict:
        with self._note._client.http() as http:
            r = http.get(f"{self._base()}{suffix}", params=params or {})
        return self._checked(r, f"list files of {self._note._id}")

    def _patch(self, file_id: str, payload: dict, *, if_match: str | None) -> NoteFile:
        headers = {"If-Match": if_match} if if_match else {}
        with self._note._client.http() as http:
            r = http.patch(f"{self._base()}/{file_id}", json=payload, headers=headers)
        return _view(self._checked(r, f"move {file_id}"), self)

    def _copy(self, file_id: str, path: str, *, overwrite: bool) -> NoteFile:
        with self._note._client.http() as http:
            r = http.post(
                f"{self._base()}/{file_id}/copy",
                json={"path": path, "overwrite": overwrite},
            )
        return _view(self._checked(r, f"copy {file_id}"), self)

    def _preview(self, file_id: str, *, share: bool) -> dict:
        with self._note._client.http() as http:
            r = http.get(
                f"{self._base()}/{file_id}/preview",
                params={"share": "true"} if share else {},
            )
        if r.status_code == 400:
            body = r.json() if r.content else {}
            if body.get("error") == "not_previewable":
                raise NotPreviewable(body.get("message") or f"{file_id} has no rendered form")
        return self._checked(r, f"preview {file_id}")

    def _unshare(self, file_id: str) -> None:
        with self._note._client.http() as http:
            r = http.delete(f"{self._base()}/{file_id}/preview")
        self._checked(r, f"unshare {file_id}")

    def _restore(self, file_id: str, path: str | None) -> NoteFile:
        with self._note._client.http() as http:
            r = http.post(
                f"{self._base()}/{file_id}/restore",
                json={"path": path} if path else {},
            )
        return _view(self._checked(r, f"restore {file_id}"), self)

    def _delete(self, file_id: str, *, purge: bool, if_match: str | None) -> None:
        headers = {"If-Match": if_match} if if_match else {}
        with self._note._client.http() as http:
            r = http.delete(
                f"{self._base()}/{file_id}",
                params={"purge": "true" if purge else "false"},
                headers=headers,
            )
        self._checked(r, f"delete {file_id}")

    def _checked(self, resp, what: str, json: bool = True):
        """Raise the error a caller can act on, rather than a status code.

        409 and the text refusal get their own types because the caller's next
        move differs: one means "say overwrite", the other means "stop treating
        this as text". Everything else goes through the module's shared
        handler so file errors read like every other DreamLake error.
        """
        if resp.status_code == 409:
            raise FileExists(self._message(resp) or f"{what}: already exists")
        if resp.status_code == 400:
            body = resp.json() if resp.content else {}
            if body.get("error") == "not_text":
                raise NotTextFile(body.get("message") or what)
        from .notes import _raise_for

        _raise_for(resp, what)
        if not json:
            return None
        return resp.json() if resp.content else {}

    @staticmethod
    def _message(resp) -> str | None:
        try:
            return resp.json().get("message")
        except Exception:
            return None

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<NoteFiles of {self._note._ns}/{self._note._id}>"
