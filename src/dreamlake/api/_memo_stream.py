"""Native Memo streaming over one authenticated connection; no HTTP chunk loop."""
from __future__ import annotations

import json
import re
import uuid
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.sync.client import connect

from .memos import PatchReceipt


def _receipt(data):
    continuation = data.get("continuation")
    if (data.get("outcome") != "accepted" or not isinstance(data.get("revision"), str)
            or not isinstance(continuation, dict) or continuation.get("revision") != data["revision"]
            or type(continuation.get("cursor")) is not int or continuation["cursor"] < 0):
        raise ValueError("Invalid native stream receipt; reconcile the pending chunk")
    return PatchReceipt(note=data["note"], mode=data["mode"], base_revision=data["baseRevision"],
                        hash=data["hash"], revision=data["revision"], outcome="accepted",
                        command_id=None, replayed=False, continuation=continuation)


def stream_insert(memo, *, chunks, cursor, revision, mode="merge", stream_id=None, intent=None):
    from ._memos_facade import MemoStreamInterrupted
    if mode not in ("merge", "exact"):
        raise ValueError("mode must be merge or exact")
    if type(cursor) is not int or cursor < 0:
        raise ValueError("cursor must be a nonnegative code-point position")
    stream_id = f"stream:{uuid.uuid4()}" if stream_id is None else stream_id
    if not isinstance(stream_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,100}", stream_id):
        raise ValueError("stream_id must contain 8–100 command-id characters")
    if intent is not None and (not isinstance(intent, str) or not intent.strip() or len(intent) > 280):
        raise ValueError("intent must contain 1–280 characters")
    client = memo._client
    path = f"{memo._base}/stream"
    url = client.dl_url.rstrip("/") + path
    request = httpx.Request("GET", url, headers=client._headers())
    client._memo_agent_headers(request)
    parts = urlsplit(url)
    ws_url = urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc, parts.path, "", ""))
    last_receipt, pending = None, None
    try:
        with connect(ws_url, additional_headers={k: v for k, v in request.headers.items() if k.lower() != "host"}, open_timeout=15,
                     close_timeout=2, max_size=1_000_000, compression=None) as socket:
            def exchange(message):
                nonlocal last_receipt, pending
                socket.send(json.dumps(message, ensure_ascii=False))
                result = json.loads(socket.recv(timeout=60))
                if result.get("type") == "error":
                    if result.get("outcome") == "accepted" and result.get("lastReceipt"):
                        last_receipt = _receipt(result["lastReceipt"])
                        pending = None
                    raise RuntimeError(f"Native stream: {result.get('error')} ({result.get('outcome')})")
                return result

            ready = exchange(dict(type="open", baseRevision=revision, cursor=cursor, mode=mode,
                                  **({"intent": intent} if intent is not None else {})))
            if ready.get("type") != "ready":
                raise ValueError("Native stream was not admitted")
            sequence = 0
            for chunk in chunks:
                if not isinstance(chunk, str):
                    raise TypeError("stream chunks must be strings")
                for offset in range(0, len(chunk), 2048):
                    text = chunk[offset:offset + 2048]
                    text.encode("utf-8", errors="strict")
                    pending = dict(stream_id=stream_id, sequence=sequence, text=text)
                    response = exchange(dict(type="insert", sequence=sequence, text=text))
                    if response.get("type") != "receipt" or response.get("sequence") != sequence:
                        raise ValueError("Invalid native stream acknowledgment")
                    last_receipt = _receipt(response["receipt"])
                    pending = None
                    sequence += 1
                    yield last_receipt
            if exchange(dict(type="end")).get("type") != "closed":
                raise ValueError("Invalid native stream completion")
    except Exception as cause:
        raise MemoStreamInterrupted(last_receipt, pending, cause) from cause
