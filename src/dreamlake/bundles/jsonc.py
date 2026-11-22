"""JSON with comments, for SOURCE bundle manifests (bundle contract v1.4/1.5).

Source manifests (an author's directory) may have comments. Stored
manifests (what a bundle version holds and what pull delivers) are plain
JSON in the stored form, see :func:`stored_form`. Hashes use stored bytes
only. This module reads local source manifests and makes the stored form.

Source manifests are strict JSON plus ``//`` line comments and ``/* */`` block
comments (like ``tsconfig.json``). Trailing commas are not allowed. The
TypeScript side uses Microsoft ``jsonc-parser`` with
``allowTrailingComma: false`` and fails on errors; this module must give the
same result. The shared vectors are in ``hash-vectors.json`` under ``jsonc``.

Rules:

* A comment starts only outside a string. ``"http://x"`` is a string.
* A line comment ends at LF or CR. A block comment ends at the first ``*/``;
  block comments do not nest. An unterminated block comment is an error.
* Each comment becomes one space (line comments keep their line end), so a
  comment separates tokens: ``1/**/2`` is an error.
* ``NaN``, ``Infinity`` and ``-Infinity`` are errors.
* Duplicate keys: the last value wins (as ``JSON.parse`` and
  ``jsonc-parser`` do).
"""

from __future__ import annotations

import json
import math
from typing import Any


class JsoncError(ValueError):
    """The text is not valid JSON with comments."""


def strip_comments(text: str) -> str:
    """Replace each comment outside strings with whitespace."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            out.append(text[i:j + 1])
            i = j + 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = i + 2
            while j < n and text[j] not in "\r\n":
                j += 1
            out.append(" ")
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end < 0:
                raise JsoncError("unterminated block comment")
            # Keep line ends so that error positions stay useful.
            out.append(" " + "".join(ch for ch in text[i:end] if ch in "\r\n"))
            i = end + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _reject_constant(name: str) -> Any:
    raise JsoncError(f"{name} is not valid JSON")


def loads(data: str | bytes) -> Any:
    """Parse JSON with comments. Raise :class:`JsoncError` on any error."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise JsoncError(f"manifest is not UTF-8: {e}") from e
    try:
        return json.loads(strip_comments(data), parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise JsoncError(str(e)) from e


def js_number(value: float) -> str:
    """ECMAScript ``Number::toString`` (what ``JSON.stringify`` writes)."""
    if isinstance(value, bool):
        raise TypeError("bool is not a number")
    if isinstance(value, int):
        if abs(value) <= 2**53:
            return str(value)
        value = float(value)  # JavaScript has only doubles
    if not math.isfinite(value):
        return "null"  # JSON.stringify writes null for NaN and Infinity
    if value == 0:
        return "0"
    if value < 0:
        return "-" + js_number(-value)
    mantissa, _, exp = repr(value).partition("e")
    int_part, _, frac = mantissa.partition(".")
    if frac == "0":
        frac = ""
    raw = int_part + frac
    stripped = raw.lstrip("0")
    n = len(int_part) - (len(raw) - len(stripped)) + (int(exp) if exp else 0)
    digits = stripped.rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + digits
    e = n - 1
    head = digits[0] + ("." + digits[1:] if k > 1 else "")
    return f"{head}e{'+' if e >= 0 else '-'}{abs(e)}"


def _stringify(value: Any, indent: str) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    inner = indent + "  "
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return "[\n" + ",\n".join(inner + _stringify(v, inner) for v in value) + "\n" + indent + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        return "{\n" + ",\n".join(
            inner + json.dumps(k, ensure_ascii=False) + ": " + _stringify(value[k], inner)
            for k in js_key_order(value)) + "\n" + indent + "}"
    raise TypeError(f"not a JSON value: {value!r}")


def _is_array_index(key: str) -> bool:
    return key.isascii() and key.isdigit() and (key == "0" or key[0] != "0") and int(key) < 2**32 - 1


def js_key_order(obj: dict) -> list[str]:
    """Key order of ``JSON.stringify(sortKeys(obj))`` in JavaScript.

    ``Object.keys(o).sort()`` sorts by UTF-16 code units. A JavaScript object
    then lists array-index keys ("0", "7", "10") first in numeric order, then
    the other keys in insertion (here: sorted) order.
    """
    keys = sorted(obj, key=lambda k: k.encode("utf-16-be"))
    index = sorted((k for k in keys if _is_array_index(k)), key=int)
    return index + [k for k in keys if not _is_array_index(k)]


def stored_form(value: Any) -> str:
    """The stored manifest text (bundle contract v1.6):
    ``JSON.stringify(sortKeys(value), null, 2) + "\\n"``. Object keys are
    sorted recursively (JavaScript order); arrays keep their order. Numbers
    use the JavaScript form (``1.0`` becomes ``1``)."""
    return _stringify(value, "") + "\n"


def to_stored(source: str | bytes) -> bytes:
    """Source manifest (comments allowed) -> stored manifest bytes."""
    return stored_form(loads(source)).encode("utf-8")


def dumps(value: Any) -> str:
    """Text for a new manifest: the stored form."""
    return stored_form(value)


#: Parse-parity cases. ``expect`` is the parsed value; ``error`` is True when
#: the text must be rejected.
PARITY_CASES: list[dict[str, Any]] = [
    {"name": "plain JSON", "text": '{"a": 1, "b": [true, null, "x"]}'},
    {"name": "line comment", "text": '{\n  // note\n  "a": 1\n}\n'},
    {"name": "block comment", "text": '{ /* note */ "a": 1 }'},
    {"name": "multi-line block comment", "text": '{\n  /*\n   * a\n   */\n  "a": 1\n}'},
    {"name": "URL with // in a string", "text": '{"url": "https://example.com/a//b"}'},
    {"name": "/* inside a string", "text": '{"s": "a /* not a comment */ b"}'},
    {"name": "// inside a string", "text": '{"s": "// not a comment"}'},
    {"name": "escaped quote then comment marker", "text": '{"s": "q\\" // still string"}'},
    {"name": "backslash at string end", "text": '{"s": "\\\\"} // comment'},
    {"name": "/* inside a block comment does not nest", "text": '{"a": /* x /* y */ 1}'},
    {"name": "comment at EOF without newline", "text": '{"a": 1}\n// end'},
    {"name": "block comment at EOF", "text": '{"a": 1} /* end */'},
    {"name": "CRLF line ends", "text": '{\r\n  // c\r\n  "a": 1,\r\n  "b": 2\r\n}\r\n'},
    {"name": "line comment ends at CR", "text": '{"a": // c\r1}'},
    {"name": "comment between key and colon", "text": '{"a" /* c */ : 1}'},
    {"name": "key order kept", "text": '{"z": 1, "a": 2, "m": 3}'},
    {"name": "non-ASCII", "text": '{"title": "Mügé 杯子"} // ü'},
    {"name": "duplicate key: last wins", "text": '{"a": 1, "a": 2}'},
    {"name": "trailing comma in object", "text": '{"a": 1,}', "error": True},
    {"name": "trailing comma in array", "text": '{"a": [1, 2,]}', "error": True},
    {"name": "unterminated block comment", "text": '{"a": 1} /* end', "error": True},
    {"name": "comment splits a number", "text": '{"a": 1/**/2}', "error": True},
    {"name": "only a comment", "text": "// nothing\n", "error": True},
    {"name": "single quotes", "text": "{'a': 1}", "error": True},
    {"name": "NaN", "text": '{"a": NaN}', "error": True},
    {"name": "hash comment", "text": '{"a": 1} # no', "error": True},
    {"name": "single slash", "text": '{"a": 1} /', "error": True},
]


#: Source -> stored form cases (Python output). The CLI agent makes the
#: same cases with Node ``JSON.stringify``; the two must be equal.
STORED_FORM_CASES: list[dict[str, Any]] = [
    {"name": "comments removed, keys sorted",
     "source": '{\n  // robot\n  "kind": "robot", /* x */ "title": "S1",\n  "uses": ["/"]\n}\n'},
    {"name": "reordered keys give the same bytes (a)", "source": '{"b": 1, "a": {"y": 2, "x": 3}}'},
    {"name": "reordered keys give the same bytes (b)", "source": '{"a": {"x": 3, "y": 2}, "b": 1}'},
    {"name": "arrays keep their order",
     "source": '{"uses": ["z/", "a/"], "ops": [{"tag": "Merge", "src": "b"}, {"src": "a", "tag": "Attach"}]}'},
    {"name": "floats in JavaScript form",
     "source": '{"a": 1.0, "b": 0.5, "c": 1e21, "d": 1e-7, "e": 123456789012345680000, "f": -0.0, '
               '"g": 1.5e300, "h": 0.000001, "i": 100, "j": 2.50}'},
    {"name": "empty containers", "source": '{"a": [], "b": {}, "c": [{}]}'},
    {"name": "non-ASCII strings and keys",
     "source": '{"título": "Mügé 杯子", "Zeta": 1, "alpha": 2, "Ärger": 3}'},
    {"name": "escapes",
     "source": '{"e": "tab\\t nl\\n quote\\" bs\\\\ ctl\\u0001 slash\\/ u\\u00e9"}'},
    {"name": "array-index keys come first in numeric order",
     "source": '{"b": 1, "10": 2, "2": 3, "a": 4, "01": 5}'},
    {"name": "CRLF source", "source": '{\r\n  "a": 1\r\n}\r\n'},
]
