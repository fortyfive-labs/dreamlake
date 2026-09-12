"""Pure OTP parsing and redacted preview; no local-store reads, code generation or upload."""
import base64
import re
from urllib.parse import urlsplit, parse_qsl, unquote


class OtpRegistration:
    def __init__(self, payload):
        self.__payload = payload

    @property
    def type(self):
        return self.__payload["type"]

    def reveal(self):
        """Explicit secret boundary. Never log the returned mapping."""
        return dict(self.__payload)

    def __repr__(self):
        return f"OtpRegistration({self.type}, [REDACTED])"


def parse_otp_uri(uri):
    try:
        if re.search(r"[\x00-\x1f\x7f]", uri) or len(uri) > 16384 or not re.match(r"^otpauth://(totp|hotp)/", uri) or re.search(r"%(?![0-9a-fA-F]{2})", uri):
            raise ValueError()
        parsed = urlsplit(uri)
        kind = parsed.hostname
        if parsed.username or parsed.password or parsed.port or parsed.fragment or kind not in ("totp", "hotp"):
            raise ValueError()
        label = unquote(parsed.path[1:], errors="strict")
        if not label or re.search(r"[\x00-\x1f\x7f]", label):
            raise ValueError()
        params = parse_qsl(parsed.query, keep_blank_values=True, errors="strict")
        allowed = {"secret", "issuer", "algorithm", "digits", "period", "counter"}
        if any(k not in allowed for k, _ in params) or len(dict(params)) != len(params):
            raise ValueError()
        query = dict(params)
        raw = query.get("secret", "").upper()
        if not re.fullmatch(r"[A-Z2-7]+={0,6}", raw):
            raise ValueError()
        seed = raw.rstrip("=")
        if len(seed) % 8 not in (0, 2, 4, 5, 7) or ("=" in raw and (len(raw) % 8 or len(raw) != ((len(seed) + 7) // 8) * 8)):
            raise ValueError()
        decoded = base64.b32decode(seed + "=" * ((-len(seed)) % 8))
        if base64.b32encode(decoded).decode().rstrip("=") != seed:
            raise ValueError()
        algorithm, digits = query.get("algorithm", "SHA1").upper(), query.get("digits", "6")
        if algorithm not in ("SHA1", "SHA256", "SHA512") or digits not in ("6", "7", "8"):
            raise ValueError()
        issuer = query.get("issuer")
        if issuer is not None and (not issuer or re.search(r"[\x00-\x1f\x7f]", issuer) or (":" in label and label.split(":")[0] != issuer)):
            raise ValueError()
        payload = dict(type=kind, seed=seed, algorithm=algorithm, digits=int(digits), label=label, active=kind == "totp")
        if issuer is not None:
            payload["issuer"] = issuer
        if kind == "totp":
            period = query.get("period", "30")
            if "counter" in query or not re.fullmatch(r"[1-9][0-9]*", period) or int(period) > 2147483647:
                raise ValueError()
            payload["period"] = int(period)
        else:
            counter = query.get("counter", "")
            if "period" in query or not re.fullmatch(r"0|[1-9][0-9]*", counter) or int(counter) > 18446744073709551615:
                raise ValueError()
            payload["counter"] = counter
        return OtpRegistration(payload)
    except Exception:
        raise ValueError("Invalid or unsupported OTP registration") from None


def preview_pass_otp(records):
    """Caller explicitly supplies decrypted records; output contains only paths/status/types/counts."""
    if len(records) > 10000:
        raise ValueError("OTP preview exceeds record limit")
    entries = []
    for record in records:
        lines = [line for line in re.split(r"\r?\n", record["content"]) if line.startswith("otpauth://")]
        entry = dict(path=record["path"], status="no-otp")
        if lines:
            entry["status"] = "invalid"
            if len(record["content"]) <= 1048576 and len(lines) == 1:
                try:
                    entry.update(status="ready", type=parse_otp_uri(lines[0]).type)
                except ValueError:
                    pass
        entries.append(entry)
    return dict(entries=entries, ready=sum(e["status"] == "ready" for e in entries), invalid=sum(e["status"] == "invalid" for e in entries), skipped=sum(e["status"] == "no-otp" for e in entries))
