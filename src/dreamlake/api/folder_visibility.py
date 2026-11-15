"""Version-checked visibility for ordinary folders and Bindrs by stable ID."""
from datetime import datetime
import re
from typing import Literal

Visibility = Literal["private", "unlisted", "public"]


class VisibilityWriteUnconfirmed(RuntimeError):
    """Read current visibility before deciding whether to retry an uncertain write."""


def _visibility(value):
    if value not in ("private", "unlisted", "public"):
        raise ValueError("visibility must be private, unlisted or public")


def _version(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if "T" not in value or stamp.tzinfo is None:
            raise ValueError()
    except (AttributeError, TypeError, ValueError):
        raise ValueError("expected_updated_at must be the timestamp from a fresh read") from None


class FolderVisibility:
    def __init__(self, client, kind="folder"):
        if kind not in ("folder", "bindr"):
            raise ValueError("Unknown folder kind")
        self.client, self.kind = client, kind

    def _path(self, object_id, read=False):
        if not isinstance(object_id, str) or not re.fullmatch(r"[a-fA-F0-9]{24}", object_id):
            raise ValueError("A 24-hex folder or Bindr ID is required")
        if self.kind == "folder":
            return f"/nodes/{object_id}/visibility"
        return f"/bindrs/{object_id}" + ("/summary" if read else "")

    def _confirmed(self, body, object_id, expected=None):
        row = body.get("bindr") if self.kind == "bindr" else body
        if not isinstance(row, dict) or row.get("id") != object_id:
            raise ValueError("Invalid folder metadata")
        _version(row.get("updatedAt"))
        if row.get("visibility") is not None:
            _visibility(row["visibility"])
        if expected is not None and row.get("visibility") != expected:
            raise ValueError("Unexpected visibility acknowledgment")
        return {key: row[key] for key in ("id", "visibility", "updatedAt", "explicitVisibility") if key in row}

    def get_visibility(self, object_id: str) -> dict:
        """Read visibility and its version without opening a Project catalog."""
        path = self._path(object_id, read=True)
        with self.client.http() as http:
            response = http.get(path, follow_redirects=False)
        response.raise_for_status()
        return self._confirmed(response.json(), object_id)

    def set_visibility(self, object_id: str, visibility: Visibility, *, expected_updated_at: str) -> dict:
        """One guarded write; never automatically retries an uncertain response."""
        _visibility(visibility)
        _version(expected_updated_at)
        path = self._path(object_id)
        try:
            with self.client.http() as http:
                response = http.patch(path, json={"visibility": visibility, "expectedUpdatedAt": expected_updated_at}, follow_redirects=False)
        except Exception as error:
            raise VisibilityWriteUnconfirmed("Visibility update was not confirmed; read current metadata before retrying") from error
        if response.status_code >= 500:
            raise VisibilityWriteUnconfirmed("Visibility update was not confirmed; read current metadata before retrying")
        response.raise_for_status()
        try:
            return self._confirmed(response.json(), object_id, visibility)
        except (ValueError, TypeError, AttributeError) as error:
            raise VisibilityWriteUnconfirmed("Visibility update was not confirmed; read current metadata before retrying") from error
