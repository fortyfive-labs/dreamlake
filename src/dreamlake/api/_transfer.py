"""Putting a file into S3 against signatures dreamlake-server minted.

This replaces the BSS handshake (`/files/upload/multipart/{init,parts,complete}`
plus a registration in BSS). BSS is being retired, and with it the second
registration: a node records ``metadata.s3Key`` and nothing has to resolve an
id through another service to read or delete the file.

Four things here are load-bearing. Each has a version that looks right and
produces a corrupt object through entirely successful requests:

**The signed headers are used verbatim.** Every header the server lists is part
of the signature. The BSS flow sent its own ``Content-Type`` on each part,
which BSS's signature tolerated and this one does not — S3 answers 403. So a
part PUT sends what it was given and nothing else.

**The completion body is XML.** ``CompleteMultipartUpload`` does not take JSON.
Without a correct part list the upload never becomes an object, and the parts
sit in the bucket billed and invisible to a listing.

**A 2xx from Complete is where it starts, not where it ends.** S3 holds the
connection open with whitespace while it assembles the parts, so the status
line is written before the outcome is known and the body may be an ``<Error>``.

**Part numbers are absolute.** Each part's byte range is derived from
``part_number * part_size``, so a renumbered list sends the wrong range under a
signature minted for another part.

The bytes are never read whole: each part is seeked and read off one open
handle. ``upload()`` previously did ``read_bytes()``, so the whole file sat in
memory and a large video could not be uploaded at all.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx

#: What `complete` must say it wants. The literal the server's contract fixes;
#: a different value is a body nobody promised to accept.
COMPLETE_BODY_FORMAT = "s3-complete-multipart-xml"
_COMPLETE_ROOT = "CompleteMultipartUploadResult"

#: Parts in flight is 1: `httpx` here is synchronous and the SDK's upload has
#: always been sequential, so this keeps the shape it had. The part size comes
#: from the server, never from a constant here — a local value could only
#: disagree with the signatures.


class TransferError(RuntimeError):
    """An upload that cannot be completed."""


@dataclass
class SignedOperation:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    method: str = "PUT"

    @staticmethod
    def parse(raw: Any, where: str) -> "SignedOperation":
        if not isinstance(raw, dict):
            raise TransferError(f"{where}: expected a signed operation")
        url = raw.get("url")
        if not isinstance(url, str) or not url:
            raise TransferError(f"{where}: missing url")
        headers = raw.get("headers")
        if not isinstance(headers, dict):
            raise TransferError(f"{where}: missing headers")
        for key, value in headers.items():
            if not isinstance(value, str):
                raise TransferError(f"{where}: header {key} is not a string")
        method = raw.get("method") or "PUT"
        return SignedOperation(url=url, headers=dict(headers), method=method)


@dataclass
class MultipartPlan:
    upload_id: str
    part_size: int
    #: Only the parts this run has to send. A resume omits the stored ones.
    parts: list[tuple[int, SignedOperation]]
    complete: SignedOperation
    abort: SignedOperation
    #: ``(part_number, etag)`` for parts a previous run already stored.
    completed: list[tuple[int, str]] = field(default_factory=list)


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    """The full sha256, streamed.

    Full, not truncated: the authorize endpoint refuses anything but 64
    lowercase hex characters, because the hash is the only client-controlled
    part of the object key and a loose one escapes the project's prefix. The
    BSS flow sent the first 16 characters.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def parse_multipart(
    transfer: Any,
    size: int,
    where: str,
    completed: Iterable[tuple[int, str]] = (),
) -> MultipartPlan:
    """Check a transfer plan against the size it was minted for.

    The part plan is the dangerous half: a gap sends the wrong bytes under a
    signature minted for another part, a duplicate sends one range twice and
    omits another, and too few parts leave the tail unsent — and in every case
    each PUT succeeds, Complete succeeds, and the object exists holding bytes
    nobody checked.

    ``completed`` makes the check span the union rather than relaxing it: a
    resume legitimately omits the parts that already landed, so what has to
    hold is "together with these, every part exactly once".
    """
    if not isinstance(transfer, dict):
        raise TransferError(f"{where}: expected an object")
    upload_id = transfer.get("uploadId")
    if not isinstance(upload_id, str) or not upload_id:
        raise TransferError(f"{where}: missing uploadId")
    part_size = transfer.get("partSize")
    if not isinstance(part_size, int) or isinstance(part_size, bool) or part_size <= 0:
        raise TransferError(f"{where}: partSize must be a positive integer")

    part_count = -(-size // part_size)  # ceil
    stored = list(completed)
    seen: set[int] = set()
    for number, _etag in stored:
        if not 1 <= number <= part_count:
            raise TransferError(
                f"{where}: stored part {number} is outside the {part_count} this file has"
            )
        if number in seen:
            raise TransferError(f"{where}: stored part {number} is listed twice")
        seen.add(number)

    raw_parts = transfer.get("parts")
    if not isinstance(raw_parts, list):
        raise TransferError(f"{where}: no parts")
    parts: list[tuple[int, SignedOperation]] = []
    for index, raw in enumerate(raw_parts):
        if not isinstance(raw, dict):
            raise TransferError(f"{where}.parts[{index}]: expected an object")
        number = raw.get("partNumber")
        if not isinstance(number, int) or isinstance(number, bool):
            raise TransferError(f"{where}.parts[{index}]: partNumber must be an integer")
        if not 1 <= number <= part_count:
            raise TransferError(
                f"{where}.parts[{index}]: partNumber {number} is outside "
                f"the {part_count} this file has"
            )
        if number in seen:
            raise TransferError(f"{where}.parts[{index}]: part {number} is already stored")
        seen.add(number)
        parts.append((number, SignedOperation.parse(raw.get("put"), f"{where}.parts[{index}].put")))

    if len(seen) != part_count:
        raise TransferError(f"{where}: the parts do not cover the file")

    complete = SignedOperation.parse(transfer.get("complete"), f"{where}.complete")
    if complete.method != "POST":
        raise TransferError(f"{where}: complete is not a POST")
    body_format = (transfer.get("complete") or {}).get("bodyFormat")
    if body_format != COMPLETE_BODY_FORMAT:
        raise TransferError(f"{where}: complete.bodyFormat is not {COMPLETE_BODY_FORMAT}")
    abort = SignedOperation.parse(transfer.get("abort"), f"{where}.abort")

    return MultipartPlan(
        upload_id=upload_id,
        part_size=part_size,
        parts=parts,
        complete=complete,
        abort=abort,
        completed=stored,
    )


def completion_body(parts: Iterable[tuple[int, str]]) -> str:
    """The XML ``CompleteMultipartUpload`` takes.

    Sorted, because S3 requires ascending part numbers. ETags are opaque and
    pass through as storage gave them, quotes and all — S3 accepts them quoted
    or bare, and a resumed upload mixes the two (fresh PUTs quoted, the ones
    the server reports as stored bare).
    """

    def escape(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    entries = "".join(
        f"<Part><PartNumber>{number}</PartNumber><ETag>{escape(etag)}</ETag></Part>"
        for number, etag in sorted(parts, key=lambda p: p[0])
    )
    return f"<CompleteMultipartUpload>{entries}</CompleteMultipartUpload>"


def _xml_root(text: str) -> str | None:
    match = re.search(r"<\s*([A-Za-z_][\w.\-]*)", text.lstrip().lstrip("﻿"))
    return match.group(1) if match else None


def put_single(
    client: httpx.Client,
    op: SignedOperation,
    path: Path,
    size: int,
) -> None:
    """One PUT for the whole object, streamed off the handle."""
    with path.open("rb") as handle:
        response = client.request(
            op.method,
            op.url,
            content=handle if size else b"",
            headers={**op.headers, "Content-Length": str(size)},
            timeout=None,
        )
    if response.status_code >= 400:
        raise TransferError(f"storage answered {response.status_code} to the upload")


def _put_part(
    client: httpx.Client,
    op: SignedOperation,
    path: Path,
    start: int,
    length: int,
    label: str,
) -> str:
    with path.open("rb") as handle:
        handle.seek(start)
        body = handle.read(length)
    if len(body) != length:
        raise TransferError(f"{label}: read {len(body)} of {length} bytes (file changed?)")
    response = client.request(
        op.method,
        op.url,
        content=body,
        # The signed headers VERBATIM, plus the length. Anything else is a
        # header the signature did not cover.
        headers={**op.headers, "Content-Length": str(length)},
        timeout=None,
    )
    if response.status_code >= 400:
        raise TransferError(f"{label}: storage answered {response.status_code}")
    etag = response.headers.get("etag")
    if not etag:
        # The PUT succeeded, so retrying returns the same missing header — on
        # real storage, a bucket whose CORS does not expose it.
        raise TransferError(f"{label}: storage returned no ETag")
    return etag


def run_multipart(
    client: httpx.Client,
    plan: MultipartPlan,
    path: Path,
    size: int,
    on_part: Any = None,
    abort_on_failure: bool = True,
) -> None:
    """Send the missing parts and complete the upload.

    ``abort_on_failure=False`` is for a caller that recorded the upload and
    will resume it: aborting would destroy exactly the parts the next run
    skips, turning an interruption into a restart from zero.
    """
    # Seeded with what a previous run stored. Completion names EVERY part or
    # the object is assembled from a subset.
    etags: list[tuple[int, str]] = list(plan.completed)
    try:
        for number, op in plan.parts:
            start = (number - 1) * plan.part_size
            length = min(plan.part_size, size - start)
            etags.append((number, _put_part(client, op, path, start, length, f"part {number}")))
            if on_part is not None:
                on_part(number)
    except Exception:
        if abort_on_failure:
            _abort(client, plan)
        raise

    try:
        response = client.request(
            plan.complete.method,
            plan.complete.url,
            content=completion_body(etags).encode(),
            headers=dict(plan.complete.headers),
            timeout=None,
        )
    except Exception:
        if abort_on_failure:
            _abort(client, plan)
        raise
    if response.status_code >= 400 or _xml_root(response.text) != _COMPLETE_ROOT:
        if abort_on_failure:
            _abort(client, plan)
        raise TransferError("storage refused to complete the upload")


def _abort(client: httpx.Client, plan: MultipartPlan) -> None:
    """Best effort. A failed abort must never replace the real error."""
    try:
        client.request(plan.abort.method, plan.abort.url, headers=dict(plan.abort.headers), timeout=30)
    except Exception:
        pass
