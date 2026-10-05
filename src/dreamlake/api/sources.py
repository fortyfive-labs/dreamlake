"""Source v1 management. Server decides authority; exact keys survive explicit retries."""
from urllib.parse import quote


class SourceError(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.code, self.status = code, status


class Sources:
    def __init__(self, client):
        self.client = client

    def _request(self, namespace, source_id, method="GET", suffix="", payload=None, key=None):
        if not namespace or not source_id:
            raise ValueError("Namespace and source ID are required")
        if method != "GET" and (not isinstance(key, str) or not key.strip() or len(key) > 255):
            raise ValueError("Supply a nonempty idempotency key of at most 255 characters")
        path = "/namespaces/" + quote(namespace, safe="") + "/sources/" + quote(source_id, safe="") + suffix
        with self.client.http() as http:
            response = http.request(method, path, json=payload, headers={"Idempotency-Key": key} if key else None)
        if not response.is_success:
            try:
                body = response.json()
                code = (body.get("error") or {}).get("code", "SOURCE_REQUEST_FAILED") if isinstance(body.get("error"), dict) else body.get("code", "SOURCE_REQUEST_FAILED")
            except ValueError:
                code = "SOURCE_REQUEST_FAILED"
            raise SourceError(code, response.status_code)
        return None if response.status_code == 204 else response.json()

    def detail(self, namespace, source_id):
        return self._request(namespace, source_id)

    def disable(self, namespace, source_id, *, idempotency_key):
        return self._request(namespace, source_id, "POST", ":disable", {}, idempotency_key)

    def enable(self, namespace, source_id, *, idempotency_key):
        return self._request(namespace, source_id, "POST", ":enable", {}, idempotency_key)

    def delete(self, namespace, source_id, *, idempotency_key):
        """Terminal delete queues cleanup; the 202 response is not completion."""
        return self._request(namespace, source_id, "DELETE", key=idempotency_key)

    def editors(self, namespace, source_id):
        return self._request(namespace, source_id, suffix="/editors")

    @staticmethod
    def _subject(subject_type, subject_id):
        if subject_type not in {"USER", "TEAM", "SERVICE_ACCOUNT"} or not subject_id:
            raise ValueError("Valid editor subject type and ID are required")

    def add_editor(self, namespace, source_id, subject_type, subject_id, *, idempotency_key):
        self._subject(subject_type, subject_id)
        return self._request(namespace, source_id, "POST", "/editors", {"subjectType": subject_type, "subjectId": subject_id}, idempotency_key)

    def remove_editor(self, namespace, source_id, subject_type, subject_id, *, idempotency_key):
        self._subject(subject_type, subject_id)
        return self._request(namespace, source_id, "DELETE", "/editors/" + subject_type + "/" + quote(subject_id, safe=""), key=idempotency_key)
