"""Annotation metadata without importing DreamDB or opening a content lease."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from ._client import DreamLakeClient


class AnnotationMetadataError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(f"({status}) {code}")


def _segment(value: str) -> str:
    if not isinstance(value, str) or not value or value in (".", "..") or any(c in value for c in "/?#\\"):
        raise ValueError("An explicit namespace and annotation name are required")
    return quote(value, safe="")


class Annotations:
    def __init__(self, client: DreamLakeClient):
        self.client = client

    def _request(self, method: str, namespace: str, name: str | None = None,
                 body: dict | None = None, share: str | None = None, *,
                 suffix: str = "", query: dict[str, str] | None = None) -> dict[str, Any]:
        path = f"/namespaces/{_segment(namespace)}/annotations"
        if name is not None:
            path += "/" + _segment(name)
        path += suffix
        params = {**(query or {}), **({"share": share} if share else {})}
        with self.client.http() as http:
            response = http.request(method, path, **({"json": body} if body is not None else {}),
                                    **({"params": params} if params else {}))
        try:
            result = response.json()
        except ValueError:
            raise AnnotationMetadataError(response.status_code, "INVALID_ANNOTATION_RESPONSE") from None
        if not response.is_success:
            raise AnnotationMetadataError(response.status_code, str(result.get("error", "ANNOTATION_REQUEST_FAILED")) if isinstance(result, dict) else "ANNOTATION_REQUEST_FAILED")
        if not isinstance(result, dict):
            raise AnnotationMetadataError(502, "INVALID_ANNOTATION_RESPONSE")
        return result

    def list(self, namespace: str) -> dict[str, Any]:
        return self._request("GET", namespace)

    def get(self, namespace: str, name: str, *, share: str | None = None) -> dict[str, Any]:
        return self._request("GET", namespace, name, share=share)

    def update(self, namespace: str, name: str, *, title: str | None = None,
               visibility: str | None = None, slug: str | None = None,
               expected_resource_id: str | None = None, expected_slug: str | None = None,
               expected_revision: int | None = None, share: str | None = None) -> dict[str, Any]:
        """Patch only supplied fields. Slug changes require an explicit read snapshot.

        Never retries a conflict or uncertain write. Old URLs and storage names
        remain valid; permissions are evaluated by the server on every request.
        """
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if visibility is not None:
            if visibility not in ("private", "unlisted", "public"):
                raise ValueError("visibility must be private, unlisted or public")
            body["visibility"] = visibility
        if slug is not None:
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", slug):
                raise ValueError("slug must be 1–64 lowercase letters, digits, dots, underscores or hyphens, starting with a letter or digit")
            if not expected_resource_id or expected_slug is None or type(expected_revision) is not int or expected_revision < 0:
                raise ValueError("slug changes require expected_resource_id, expected_slug and expected_revision")
            body.update(slug=slug, expectedResourceId=expected_resource_id,
                        expectedSlug=expected_slug, expectedRevision=expected_revision)
        return self._request("PATCH", namespace, name, body, share)

    def rename(self, namespace: str, name: str, slug: str) -> dict[str, Any]:
        """Read the current slug revision, then submit one guarded rename."""
        row = self.get(namespace, name)
        if not row.get("id") or not row.get("slug") or type(row.get("slugRevision")) is not int:
            raise AnnotationMetadataError(501, "ANNOTATION_SLUG_UNSUPPORTED")
        return self.update(namespace, name, slug=slug, expected_resource_id=row["id"],
                           expected_slug=row["slug"], expected_revision=row["slugRevision"])


    def get_share(self, namespace: str, name: str) -> dict[str, Any]:
        """Read current sharing settings; requires sharing management permission."""
        return self._request("GET", namespace, name, suffix="/share")

    def set_share(self, namespace: str, name: str, role: str = "read") -> dict[str, Any]:
        """Enable the signed-in link or change its live role without changing visibility."""
        if role not in ("read", "write"):
            raise ValueError("role must be read or write")
        return self._request("PUT", namespace, name, {"role": role}, suffix="/share")

    def revoke_share(self, namespace: str, name: str, *, revoke_accepted: bool = False) -> dict[str, Any]:
        """Disable link-derived access. Existing content leases last until expiry.

        Retained acceptances regain access if sharing is re-enabled, unless
        revoke_accepted=True deletes them. Never retries an uncertain write.
        """
        return self._request("DELETE", namespace, name, suffix="/share",
                             query={"revokeAccepted": "true"} if revoke_accepted else None)

    def share_access(self, namespace: str, name: str) -> dict[str, Any]:
        """List retained recipients and the current link role (null when disabled)."""
        return self._request("GET", namespace, name, suffix="/share/access")

    def remove_share_access(self, namespace: str, name: str, user_id: str) -> dict[str, Any]:
        """Remove one acceptance, not a block: a live link can re-admit the user."""
        return self._request("DELETE", namespace, name, suffix="/share/access/" + _segment(user_id))
