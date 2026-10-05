"""Ownership v1. Requests and retries keep the caller's exact preview and key."""
from urllib.parse import quote


class OwnershipError(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.code = code
        self.status = status


class Ownership:
    def __init__(self, client):
        self.client = client

    def _request(self, namespace, method, suffix, payload=None, params=None):
        if not namespace:
            raise ValueError("Namespace is required")
        path = "/namespaces/" + quote(namespace, safe="") + "/ownership/" + suffix
        with self.client.http() as http:
            response = http.request(method, path, json=payload, params=params)
        if not response.is_success:
            try:
                code = response.json().get("code", "OWNERSHIP_REQUEST_FAILED")
            except ValueError:
                code = "OWNERSHIP_REQUEST_FAILED"
            raise OwnershipError(code, response.status_code)
        body = response.json()
        if body.get("apiVersion") != 1:
            raise OwnershipError("UNSUPPORTED_API_VERSION", response.status_code)
        return body

    def capabilities(self, namespace):
        return self._request(namespace, "GET", "capabilities")

    def preview(self, namespace, request):
        return self._request(namespace, "POST", "transfers/preview", request)

    def create(self, namespace, request):
        return self._request(namespace, "POST", "transfers", request)

    def list(self, namespace, direction="incoming"):
        if direction not in {"incoming", "outgoing"}:
            raise ValueError("Direction must be incoming or outgoing")
        return self._request(namespace, "GET", "transfers", params={"direction": direction})

    def status(self, namespace, operation_id):
        return self._request(namespace, "GET", "transfers/" + quote(operation_id, safe=""))

    def _decide(self, namespace, operation_id, decision):
        return self._request(namespace, "POST", "transfers/" + quote(operation_id, safe="") + "/" + decision)

    def accept(self, namespace, operation_id):
        return self._decide(namespace, operation_id, "accept")

    def reject(self, namespace, operation_id):
        return self._decide(namespace, operation_id, "reject")

    def cancel(self, namespace, operation_id):
        return self._decide(namespace, operation_id, "cancel")

    def _resource(self, namespace, resource_type, resource_id, action=None):
        if resource_type not in {"project", "source"}:
            raise ValueError("Resource type must be project or source")
        import re
        if not re.fullmatch(r"[a-fA-F0-9]{24}", resource_id):
            raise ValueError("A valid resource ID is required")
        suffix = "resources/" + resource_type + "/" + quote(resource_id, safe="")
        if action:
            suffix += "/" + action
        return self._request(namespace, "POST" if action == "restore" else "GET", suffix)

    def inspect(self, namespace, resource_type, resource_id):
        return self._resource(namespace, resource_type, resource_id)

    def manifest(self, namespace, resource_type, resource_id):
        return self._resource(namespace, resource_type, resource_id, "manifest")

    def restore(self, namespace, resource_type, resource_id):
        """Explicit unsupported response until project snapshots exist; Source deletion is terminal."""
        return self._resource(namespace, resource_type, resource_id, "restore")
