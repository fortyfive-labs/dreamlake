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

    def _request(self, namespace, project, method="GET", suffix="", payload=None, params=None):
        if not namespace or not project:
            raise ValueError("An explicit namespace and project slug are required")
        path = "/namespaces/" + quote(namespace, safe="") + "/projects/" + quote(project, safe="") + suffix
        with self.client.http() as http:
            response = http.request(method, path, json=payload, **({"params": params} if params is not None else {}))
        if not response.is_success:
            try:
                body = response.json()
                code = (body.get("code") or body.get("error")) if isinstance(body, dict) else None
                if isinstance(code, dict):
                    code = code.get("code")
            except ValueError:
                code = None
            raise ProjectAccessError(response.status_code, code if isinstance(code, str) and code else "PROJECT_ACCESS_FAILED")
        try:
            return response.json() if response.content else None
        except ValueError:
            raise ProjectAccessError(response.status_code, "INVALID_PROJECT_RESPONSE") from None

    def inspect(self, namespace, project):
        return self._request(namespace, project)

    def resources(self, namespace, project, *, include_archived=False,
                  parent=None, bindr=None, resource_type=None, search=None,
                  page=1, page_size=50, sort=None):
        """List project/folder resources or bindr members with server pagination.

        Archived notes, folders and descendants of archived folders are hidden
        unless include_archived=True. Bindr associations remain intact.
        """
        params = {"includeArchived": str(include_archived).lower(),
                  "page": page, "pageSize": page_size}
        for key, value in (("parent", parent), ("bindr", bindr),
                           ("type", resource_type), ("search", search), ("sort", sort)):
            if value is not None:
                params[key] = value
        return self._request(namespace, project, suffix="/resources", params=params)

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

    def transfer_plan(self, namespace, project):
        """Resolve a Project name and read its complete server-authorized transfer inventory."""
        from .project_transfer import validate_plan
        resource = self.inspect(namespace, project)
        resource_id = resource.get("id") if isinstance(resource, dict) else None
        if not isinstance(resource_id, str) or not re.fullmatch(r"[a-fA-F0-9]{24}", resource_id):
            from .ownership import OwnershipError
            raise OwnershipError("INVALID_PROJECT_IDENTITY", 502)
        return validate_plan(self.client.ownership.transfer_plan(namespace, resource_id), resource_id)

    def move(self, namespace, project, to_namespace, *, dry_run=True, confirm=False,
             preview_hash=None, idempotency_key=None, preserve_access=False,
             preserve_external_filings=False, destination_name=None, destination_names=None,
             reviewed=None, accept_if_owner=False, wait=False, wait_timeout=30):
        """Preview a whole Project with its owned Notes, Artifacts and Envs; confirmation/hash is required."""
        from .project_transfer import move_project
        return move_project(self, namespace, project, to_namespace, dry_run=dry_run, confirm=confirm,
                            preview_hash=preview_hash, idempotency_key=idempotency_key,
                            preserve_access=preserve_access, preserve_external_filings=preserve_external_filings,
                            destination_name=destination_name, destination_names=destination_names, reviewed=reviewed,
                            accept_if_owner=accept_if_owner, wait=wait, wait_timeout=wait_timeout)
