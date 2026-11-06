"""Artifact catalog access and visibility, using the caller's existing identity."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

if TYPE_CHECKING:
    from ._client import DreamLakeClient

ArtifactVisibility = Literal["private", "public", "unlisted"]


class ArtifactError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _segment(value: str) -> str:
    if not isinstance(value, str) or value in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        raise ValueError("An explicit namespace or artifact slug is required")
    return quote(value, safe="")


class Artifacts:
    def __init__(self, client: DreamLakeClient):
        self.client = client

    def _request(self, namespace: str, artifact: str | None = None, *,
                 visibility: ArtifactVisibility | None = None) -> dict[str, Any]:
        path = f"/namespaces/{_segment(namespace)}/artifacts"
        if artifact is not None:
            path += "/" + _segment(artifact)
        with self.client.http() as http:
            response = http.request("GET" if visibility is None else "POST", path,
                                    **({} if visibility is None else {"json": {"visibility": visibility}}))
        if not response.is_success:
            raise ArtifactError(response.status_code, "ARTIFACT_REQUEST_FAILED")
        try:
            body = response.json()
        except ValueError:
            raise ArtifactError(response.status_code, "INVALID_ARTIFACT_RESPONSE") from None
        if not isinstance(body, dict):
            raise ArtifactError(response.status_code, "INVALID_ARTIFACT_RESPONSE")
        return body

    def list(self, namespace: str) -> dict[str, Any]:
        """List the caller-visible catalog; members retain their unlisted artifacts."""
        return self._request(namespace)

    def get(self, namespace: str, artifact: str) -> dict[str, Any]:
        """Read one artifact's catalog metadata without altering sharing."""
        return self._request(namespace, artifact)

    def set_visibility(self, namespace: str, artifact: str,
                       visibility: ArtifactVisibility) -> dict[str, Any]:
        """Change existing artifact visibility without uploading a new version.

        Unlisted permits anonymous direct-link reads and hides public listings.
        Omitted fields, including share tokens, are preserved. Requests are never
        retried automatically; after an uncertain write, read get() to reconcile.
        """
        if visibility not in ("private", "public", "unlisted"):
            raise ValueError("visibility must be private, public or unlisted")
        row = self.get(namespace, artifact)
        if row.get("viewerCanManage") is not True:
            raise ArtifactError(403, "ARTIFACT_MANAGEMENT_DENIED")
        resolved = row.get("artifactId")
        if not isinstance(resolved, str):
            raise ArtifactError(502, "INVALID_ARTIFACT_RESPONSE")
        result = self._request(namespace, resolved, visibility=visibility)
        if result.get("visibility") != visibility:
            raise ArtifactError(502, "INVALID_ARTIFACT_RESPONSE")
        return result
