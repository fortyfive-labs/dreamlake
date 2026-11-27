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
        return self._request_path(path, method, payload, params)

    def _request_path(self, path, method="GET", payload=None, params=None):
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

    def update(self, namespace, project, *, name=None, slug=None, slug_mode=None,
               expected_project_id=None, expected_slug_revision=None):
        """Rename or set the URL policy using a verified stable ID and revision.

        Name-only edits follow auto mode; custom slugs persist. Reset with
        slug_mode="auto". Old browser links are preserved by the server.
        Unsupported servers and stale edits fail without automatic retry.
        """
        self._access_target(namespace, project)
        if slug_mode not in (None, "auto", "custom"):
            raise ValueError("slug_mode must be auto or custom")
        if slug is not None and slug_mode == "auto":
            raise ValueError("slug and slug_mode=auto cannot be combined")
        if name is None and slug is None and slug_mode is None:
            raise ValueError("Supply name, slug, or slug_mode")
        if expected_slug_revision is not None and (
                type(expected_slug_revision) is not int or expected_slug_revision < 0):
            raise ValueError("expected_slug_revision must be a nonnegative integer")
        current = self.inspect(namespace, project)
        if (not isinstance(current, dict) or not isinstance(current.get("id"), str)
                or not re.fullmatch(r"[a-fA-F0-9]{24}", current["id"])
                or current.get("slugMode") not in ("auto", "custom")
                or type(current.get("slugRevision")) is not int or current["slugRevision"] < 0):
            raise ProjectAccessError(501, "PROJECT_SLUG_SETTINGS_UNSUPPORTED")
        if expected_project_id is not None and current["id"] != expected_project_id:
            raise ProjectAccessError(409, "PROJECT_REPLACED")
        payload = {"expectedProjectId": current["id"], "expectedSlugRevision":
                   current["slugRevision"] if expected_slug_revision is None else expected_slug_revision}
        for key, value in (("name", name), ("slug", slug), ("slugMode", slug_mode)):
            if value is not None:
                payload[key] = value
        result = self._request(namespace, current["id"], "PATCH", payload=payload)
        expected_mode = slug_mode or ("custom" if slug is not None else current["slugMode"])
        expected_slug = slug if slug is not None else current.get("slug") if expected_mode == "custom" else None
        # Match the server's ECMAScript trim for a changed display name. Existing
        # unchanged legacy names are retained by the server's field validator.
        trim_chars = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
        expected_name = name if name == current.get("name") else name.strip(trim_chars) if isinstance(name, str) else name
        if (not isinstance(result, dict) or result.get("id") != current["id"]
                or not isinstance(result.get("slug"), str) or not result["slug"]
                or re.search(r"[,/]", result["slug"]) or result["slug"] in (".", "..")
                or result.get("slugMode") != expected_mode
                or type(result.get("slugRevision")) is not int
                or result["slugRevision"] < current["slugRevision"]
                or (expected_slug is not None and result["slug"] != expected_slug)
                or (name is not None and result.get("name") != expected_name)):
            raise ProjectAccessError(503, "PROJECT_UPDATE_UNCONFIRMED")
        return result

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

    @staticmethod
    def _access_target(namespace, project):
        if any(not isinstance(value, str) or not value.strip() for value in (namespace, project)):
            raise ValueError("An explicit namespace and project ID or slug are required")

    @staticmethod
    def _same_project(value, project):
        """Whether a response names the requested Project, by scoped ID or slug."""
        if re.fullmatch(r"[a-fA-F0-9]{24}", project):
            return value["projectId"].lower() == project.lower()
        return value["projectSlug"] == project

    @staticmethod
    def _access_policy(value, namespace, project, *, mode=None, visibility=None):
        """Do not present an unconfirmed or wrong-resource policy as success."""
        valid = (
            isinstance(value, dict)
            and isinstance(value.get("projectId"), str)
            and re.fullmatch(r"[a-fA-F0-9]{24}", value["projectId"]) is not None
            and value.get("namespace") == namespace
            and isinstance(value.get("projectSlug"), str)
            and bool(value["projectSlug"])
            and value.get("accessMode") in ("workspace", "restricted")
            and value.get("visibility") in ("private", "unlisted", "public")
            and value.get("viewerRole") in ("READ", "WRITE", "ADMIN")
            and isinstance(value.get("manageGrants"), bool)
            and value["manageGrants"] == (value["viewerRole"] == "ADMIN")
        )
        if valid:
            expected = Projects._same_project(value, project)
            baseline = value.get("baseline")
            valid = (
                expected
                and isinstance(baseline, dict)
                and baseline.get("namespaceOwners") == "ADMIN"
                and "orgMembers" in baseline and baseline["orgMembers"] in ("READ", None)
                and "public" in baseline and baseline["public"] in ("READ", None)
                and not (value["accessMode"] == "restricted" and (
                    value["visibility"] != "private" or baseline["orgMembers"] is not None or baseline["public"] is not None
                ))
                and (mode is None or value["accessMode"] == mode)
                and (visibility is None or value["visibility"] == visibility)
            )
        if not valid:
            raise ProjectAccessError(502, "INVALID_PROJECT_ACCESS_RESPONSE")
        return value

    def access(self, *, namespace, project):
        """Read the server-authorized Project policy, by scoped ID or slug.

        This is distinct from listing grants: workspace/public baseline access
        may remain even when no explicit grant exists. Requires a compatible
        Project-access server; an unsupported endpoint never falls back.
        """
        self._access_target(namespace, project)
        value = self._request(namespace, project, suffix="/access")
        return self._access_policy(value, namespace, project)

    def set_access(self, *, namespace, project, access_mode, visibility=None):
        """Set Project baseline policy under current ADMIN authority.

        Restricted mode excludes workspace-member and public baseline access;
        explicit user/team grants and namespace-owner authority remain. Turning
        a public or unlisted Project restricted requires explicit ``visibility="private"``
        in the same call. Independent resource shares are unchanged. No local
        role/permission inference and no automatic mutation retry are used.
        """
        self._access_target(namespace, project)
        if access_mode not in ("workspace", "restricted"):
            raise ValueError("access_mode must be workspace or restricted")
        if visibility is not None and visibility not in ("private", "unlisted", "public"):
            raise ValueError("visibility must be private, unlisted or public")
        if access_mode == "restricted" and visibility is not None and visibility != "private":
            raise ValueError("A restricted Project must be private")
        payload = {"accessMode": access_mode}
        if visibility is not None:
            payload["visibility"] = visibility
        value = self._request(namespace, project, "PATCH", "/access", payload)
        return self._access_policy(value, namespace, project, mode=access_mode, visibility=visibility)

    def create(self, *, namespace, name, slug=None, description=None,
               access_mode="workspace", visibility="private"):
        """Create with the intended policy in the same server transaction.

        Restricted creation checks explicit atomic-create capability first;
        there is no create-then-restrict fallback or automatic write retry.
        An unconfirmed result exposes any returned ID for reconciliation.
        """
        self._access_target(namespace, name)
        if slug is not None and (not isinstance(slug, str) or not slug.strip()):
            raise ValueError("slug must be a nonempty string")
        if description is not None and not isinstance(description, str):
            raise ValueError("description must be a string")
        if access_mode not in ("workspace", "restricted"):
            raise ValueError("access_mode must be workspace or restricted")
        if visibility not in ("private", "unlisted", "public"):
            raise ValueError("visibility must be private, unlisted or public")
        if access_mode == "restricted" and visibility is not None and visibility != "private":
            raise ValueError("A restricted Project must be private")
        path = "/namespaces/" + quote(namespace, safe="") + "/projects"
        if access_mode == "restricted":
            catalog = self._request_path(path, params={"pageSize": 1})
            capability = catalog.get("accessPolicy") if isinstance(catalog, dict) else None
            if not (
                isinstance(capability, dict)
                and type(capability.get("apiVersion")) is int and capability["apiVersion"] == 1
                and capability.get("atomicCreate") is True
                and isinstance(capability.get("modes"), list)
                and all(mode in capability["modes"] for mode in ("workspace", "restricted"))
            ):
                raise ProjectAccessError(501, "PROJECT_ACCESS_UNSUPPORTED")
        payload = {"name": name, "visibility": visibility, "accessMode": access_mode}
        if slug is not None:
            payload["slug"] = slug
        if description is not None:
            payload["description"] = description
        try:
            value = self._request_path(path, "POST", payload)
        except ProjectAccessError as exc:
            if not (200 <= exc.status < 300 and exc.code == "INVALID_PROJECT_RESPONSE"):
                raise
            error = ProjectAccessError(502, "PROJECT_CREATE_UNCONFIRMED")
            error.outcome = "unknown"
            error.project_id = None
            raise error from None
        if not (
            isinstance(value, dict)
            and isinstance(value.get("id"), str)
            and re.fullmatch(r"[a-fA-F0-9]{24}", value["id"]) is not None
            and isinstance(value.get("slug"), str) and bool(value["slug"])
            and (slug is None or value["slug"] == slug)
            and value.get("accessMode") == access_mode
            and value.get("visibility") == visibility
        ):
            error = ProjectAccessError(502, "PROJECT_CREATE_UNCONFIRMED")
            error.outcome = "unknown"
            error.project_id = value.get("id") if isinstance(value, dict) and isinstance(value.get("id"), str) else None
            raise error
        return value

    # -- Cross-namespace Project sharing ---------------------------------

    @staticmethod
    def _share_text(label, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(label + " must be a nonempty string")

    @staticmethod
    def _share_namespace(label, value):
        """Namespace slug as Project moves take it: trimmed, optional ``@`` removed."""
        Projects._share_text(label, value)
        slug = value.strip().lstrip("@")
        if not slug:
            raise ValueError(label + " must be a nonempty string")
        return slug

    @staticmethod
    def _share_valid(value):
        return (
            isinstance(value, dict)
            and isinstance(value.get("id"), str) and bool(value["id"])
            and isinstance(value.get("projectId"), str)
            and re.fullmatch(r"[a-fA-F0-9]{24}", value["projectId"]) is not None
            and isinstance(value.get("projectSlug"), str) and bool(value["projectSlug"])
            and isinstance(value.get("sourceNamespace"), str) and bool(value["sourceNamespace"])
            and isinstance(value.get("targetNamespace"), str) and bool(value["targetNamespace"])
            and value["sourceNamespace"] != value["targetNamespace"]
            # Active, a first invitation, or a READ share with a pending upgrade.
            and (value.get("role", ""), value.get("proposedRole", "")) in (
                ("READ", None), ("WRITE", None), (None, "READ"), (None, "WRITE"), ("READ", "WRITE"))
            and type(value.get("revision")) is int and value["revision"] >= 0
        )

    def _share_list(self, value, accept):
        """Do not present a malformed or wrong-scope listing as the share state."""
        shares = value.get("shares") if isinstance(value, dict) else None
        if not isinstance(shares, list) or not all(self._share_valid(s) and accept(s) for s in shares):
            raise ProjectAccessError(502, "INVALID_PROJECT_SHARE_RESPONSE")
        return value

    def _share_write(self, path, payload, confirmed):
        """Send one share mutation; an unverifiable receipt is an unknown outcome."""
        try:
            value = self._request_path(path, "POST", payload)
        except ProjectAccessError as exc:
            if not (200 <= exc.status < 300 and exc.code == "INVALID_PROJECT_RESPONSE"):
                raise
            value = None
        if not (self._share_valid(value) and confirmed(value)):
            error = ProjectAccessError(502, "PROJECT_SHARE_UNCONFIRMED")
            error.outcome = "unknown"
            error.share_id = value.get("id") if isinstance(value, dict) and isinstance(value.get("id"), str) else None
            raise error
        return value

    def namespace_shares(self, *, namespace, project):
        """List this Project's shares to other namespaces, pending and active.

        ``namespace`` is the owning (source) namespace and ``project`` its
        scoped ID or slug; the server requires Project ADMIN. Returns
        ``{"shares": [...]}``. Each share carries ``id``, ``projectId``,
        ``projectSlug``, ``sourceNamespace``, ``targetNamespace``, ``role``,
        ``proposedRole`` and ``revision``. ``role`` is the role in effect
        (``None`` while the first invitation is pending); ``proposedRole`` is
        a first invitation or a READ-to-WRITE upgrade awaiting the recipient
        owner, otherwise ``None``. A pending upgrade keeps READ in effect
        until it is accepted. Namespaces are trimmed and may carry a leading
        ``@``, as in ``move``.
        """
        namespace = self._share_namespace("namespace", namespace)
        self._access_target(namespace, project)
        value = self._request(namespace, project, suffix="/namespace-shares")
        return self._share_list(value, lambda s: s["sourceNamespace"] == namespace
                                and self._same_project(s, project))

    def share_with_namespace(self, *, namespace, project, target_namespace, role="READ"):
        """Invite another namespace (personal or organization) to this Project.

        Requires Project ADMIN in the source ``namespace``. ``role`` is
        ``"READ"`` (the default) or ``"WRITE"``; ADMIN cannot be shared and
        sharing never grants execution. A new share takes effect only when an
        OWNER of ``target_namespace`` accepts it. For an existing share, a
        downgrade from WRITE to READ applies immediately, while an upgrade
        from READ to WRITE is a proposal: READ stays in effect until the
        recipient OWNER accepts. Sharing with the source namespace itself is
        rejected before any request. Independent user/team grants and
        resource shares are unchanged.

        One POST is sent and never retried. After a timeout or
        ``PROJECT_SHARE_UNCONFIRMED`` (``outcome="unknown"``, ``share_id`` when
        known), read ``namespace_shares`` before deciding whether to resubmit.
        """
        namespace = self._share_namespace("namespace", namespace)
        target_namespace = self._share_namespace("target_namespace", target_namespace)
        self._access_target(namespace, project)
        if role not in ("READ", "WRITE"):
            raise ValueError("role must be READ or WRITE")
        if target_namespace == namespace:
            raise ValueError("A Project cannot be shared with its own namespace")
        return self._share_write(
            "/namespaces/" + quote(namespace, safe="") + "/projects/"
            + quote(project, safe="") + "/namespace-shares",
            {"targetNamespace": target_namespace, "role": role},
            lambda s: s["sourceNamespace"] == namespace and s["targetNamespace"] == target_namespace
            and self._same_project(s, project) and role in (s["role"], s["proposedRole"]))

    def revoke_namespace_share(self, *, namespace, project, share_id):
        """Withdraw a pending invitation or revoke an active namespace share.

        Requires Project ADMIN in the source ``namespace``. Removes only the
        access derived from this share; other grants remain. Returns ``None``.
        One DELETE is sent and never retried: after a timeout, read
        ``namespace_shares`` to learn whether the share is gone.
        """
        namespace = self._share_namespace("namespace", namespace)
        self._access_target(namespace, project)
        self._share_text("share_id", share_id)
        self._request(namespace, project, "DELETE", "/namespace-shares/" + quote(share_id, safe=""))

    def shared_projects(self, *, namespace):
        """List Projects other namespaces share with the recipient ``namespace``.

        Returns ``{"shares": [...]}`` with the same share fields as
        ``namespace_shares``. Members see active shares; pending invitations
        and pending upgrades are returned to namespace OWNERs only. Build
        links from the stable ``projectId`` and ``sourceNamespace``: the
        Project stays in its source namespace.
        """
        namespace = self._share_namespace("namespace", namespace)
        value = self._request_path("/namespaces/" + quote(namespace, safe="") + "/shared-projects")
        return self._share_list(value, lambda s: s["targetNamespace"] == namespace)

    def accept_shared_project(self, *, namespace, share_id, revision):
        """Accept the invitation or upgrade that was reviewed, as OWNER.

        ``revision`` is required: pass the ``revision`` of the share as read
        from ``shared_projects``. It is sent unchanged in the single request;
        the SDK never reads or substitutes a newer one, and the server decides
        whether it still matches. Returns the share, which must then be active
        with no remaining proposal.

        One POST is sent and never retried. After a timeout or
        ``PROJECT_SHARE_UNCONFIRMED`` (``outcome="unknown"``), read
        ``shared_projects`` before acting again.
        """
        namespace = self._share_namespace("namespace", namespace)
        self._share_text("share_id", share_id)
        if type(revision) is not int or revision < 0:
            raise ValueError("revision must be the nonnegative integer revision that was reviewed")
        path = ("/namespaces/" + quote(namespace, safe="") + "/shared-projects/"
                + quote(share_id, safe="") + "/accept")
        return self._share_write(
            path, {"revision": revision},
            lambda s: s["id"] == share_id and s["targetNamespace"] == namespace
            and s["role"] is not None and s["proposedRole"] is None)

    def decline_shared_project(self, *, namespace, share_id):
        """Decline a pending invitation, or leave an active share, as recipient.

        Removes the whole share for ``namespace``, including any role already
        in effect; it does not touch the source Project or other grants.
        Returns ``None``. One DELETE is sent and never retried: after a
        timeout, read ``shared_projects`` to learn whether the share is gone.
        """
        namespace = self._share_namespace("namespace", namespace)
        self._share_text("share_id", share_id)
        self._request_path("/namespaces/" + quote(namespace, safe="") + "/shared-projects/"
                           + quote(share_id, safe=""), "DELETE")

    def leave_shared_project(self, *, namespace, share_id):
        """Leave a Project shared with ``namespace``; same request as ``decline_shared_project``."""
        self.decline_shared_project(namespace=namespace, share_id=share_id)

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
        if resource_type not in {"note", "artifact", "annotation", "workflow", "env", "source", "runnable", "host"}:
            raise ValueError("Unsupported resource type")
        if not re.fullmatch(r"[a-fA-F0-9]{24}", resource_id):
            raise ValueError("A resource database ID is required")
        if resource_type == "source":
            return self._request(namespace, project, "DELETE" if detach else "PUT",
                                 "/sources/" + quote(resource_id, safe=""))
        return self._request(namespace, project, "DELETE" if detach else "POST", "/resources",
                             {"resourceType": resource_type, "resourceId": resource_id})

    def attach(self, namespace, project, resource_type, resource_id):
        """Requires Project create and the resource's sharing-management authority.

        ``"host"`` files a Host by its Host ID: the caller must be the exact Host
        owner with Project WRITE in the same namespace. A filed Host is a
        placement only: organization-member readers of the Project see its
        name and owner, never terminal/SSH or any other Host access. Connection
        access stays with ``client.hosts.grant_access``. Idempotent; the
        returned ``nodeId`` can be tagged into a bindr.
        """
        return self._attachment(namespace, project, resource_type, resource_id, detach=False)

    def detach(self, namespace, project, resource_type, resource_id):
        """Removes only access inherited through this attachment, not other grants.

        For ``"host"``, Project content-delete or the Host owner may unfile; the
        node's bindr tags go with it and the Host and its grants are unchanged.
        """
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
