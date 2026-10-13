"""Project grants and live resource attachments. Independent links are unchanged."""
from urllib.parse import quote
import re


class ProjectAccessError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


class Projects:
    def __init__(self, client):
        self.client = client

    def _request(self, namespace, project, method="GET", suffix="", payload=None):
        if not namespace or not project:
            raise ValueError("An explicit namespace and project slug are required")
        path = "/namespaces/" + quote(namespace, safe="") + "/projects/" + quote(project, safe="") + suffix
        with self.client.http() as http:
            response = http.request(method, path, json=payload)
        if not response.is_success:
            try:
                body = response.json()
                code = body.get("code") or body.get("error")
                if isinstance(code, dict):
                    code = code.get("code")
            except ValueError:
                code = None
            raise ProjectAccessError(response.status_code, code or "PROJECT_ACCESS_FAILED")
        return response.json() if response.content else None

    def inspect(self, namespace, project):
        return self._request(namespace, project)

    def grants(self, namespace, project):
        return self._request(namespace, project, suffix="/grants")

    def grant(self, namespace, project, role, *, user=None, team=None):
        if role not in {"READ", "WRITE", "ADMIN"} or bool(user) == bool(team):
            raise ValueError("Choose READ/WRITE/ADMIN and exactly one user or team")
        return self._request(namespace, project, "POST", "/grants",
                             {"role": role, **({"granteeSlug": user.lstrip("@")} if user else {"teamId": team})})

    def revoke(self, namespace, project, grant_id):
        if not grant_id:
            raise ValueError("A grant ID is required")
        return self._request(namespace, project, "DELETE", "/grants/" + quote(grant_id, safe=""))

    def _attachment(self, namespace, project, resource_type, resource_id, *, detach):
        if resource_type not in {"note", "artifact", "annotation", "workflow", "env", "source"}:
            raise ValueError("Unsupported resource type")
        if not re.fullmatch(r"[a-fA-F0-9]{24}", resource_id):
            raise ValueError("A resource database ID is required")
        if resource_type == "source":
            return self._request(namespace, project, "DELETE" if detach else "PUT",
                                 "/sources/" + quote(resource_id, safe=""))
        return self._request(namespace, project, "DELETE" if detach else "POST", "/resources",
                             {"resourceType": resource_type, "resourceId": resource_id})

    def attach(self, namespace, project, resource_type, resource_id):
        """Requires Project create and the resource's sharing-management authority."""
        return self._attachment(namespace, project, resource_type, resource_id, detach=False)

    def detach(self, namespace, project, resource_type, resource_id):
        """Removes only access inherited through this attachment, not other grants."""
        return self._attachment(namespace, project, resource_type, resource_id, detach=True)
