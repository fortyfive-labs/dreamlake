# Project grants and attachments

Available in Python SDK 0.29.0 with the companion Project-sharing backend release.
Project visibility alone does not expose private resources. Explicit grants include
current and future attached content; independent resource sharing remains valid.

```python
from dreamlake import DreamLakeClient

client = DreamLakeClient()
client.projects.inspect("acme", "research")
client.projects.grant("acme", "research", "READ", user="ge")
client.projects.attach("acme", "research", "note", note_id)
client.projects.detach("acme", "research", "note", note_id)
client.projects.revoke("acme", "research", grant_id)
```

Attachments take the resource database ID and support note, artifact, annotation,
workflow, env and source. Source uses its whole-Source reference. Attaching
requires Project content-create and independent resource sharing-management
permissions. Detachment/revocation removes only the corresponding access source;
other Project grants, namespace membership and independent share links remain.
Project ADMIN never changes ownership or grants infrastructure credential or
resource-sharing management. Resource-scoped temporary upload credentials follow
the supported content-write policy.

The SDK sends exact scoped requests without automatic mutation retries or copied
share links. `ProjectAccessError` reports the server denial and HTTP status.

Deploy the companion backend first. Project-only uploads require resource-scoped
temporary credentials; static custom-S3/MinIO or an unscoped STS fallback returns
`DELEGATED_UPLOAD_UNSUPPORTED` (403). Existing explicit grants cover existing
attachments immediately after backend release. This SDK release is optional for
Web use and does not replace the server's authorization checks.

## Workspace or restricted Project access

The following methods require the companion Project-access-policy backend.
They are new SDK source APIs; merging these docs does not prove a package or
server release. Existing `inspect`, `grants`, `grant` and `revoke` calls remain
compatible.

```python
from dreamlake import DreamLakeClient

client = DreamLakeClient()
policy = client.projects.access(namespace="acme", project=project_id)
print(policy["accessMode"], policy["baseline"], policy["viewerRole"])

# Explicitly narrow a public Project in one authorized operation.
policy = client.projects.set_access(
    namespace="acme",
    project=project_id,
    access_mode="restricted",
    visibility="private",
)

# Reuse the current user/team grants; IDs are not implicit authority.
client.projects.grant("acme", project_id, "WRITE", team=team_id)
client.projects.grant("acme", project_id, "READ", user="alice")
```

`access()` and `set_access()` take keyword-only arguments. A Project database ID
or slug is accepted within the explicit source namespace. Namespace is lookup
context, not proof of access. Python `access_mode` maps to the shared wire field
`accessMode`; role and visibility strings match the other clients.

`workspace` keeps the existing workspace-member and public baseline rules.
`restricted` excludes those baselines while retaining namespace-owner authority
and active explicit user/team grants. A restricted Project cannot be public.
If the current Project is public, explicitly pass `visibility="private"` with
`access_mode="restricted"` to change both atomically. Omitting visibility leaves
it unchanged; the SDK never silently privatizes or publishes a Project. `visibility`
is optional, accepts `private` or `public`, and cannot be `public` with restricted
mode. There is no new namespace-wide default setting in this feature.

The response carries `projectId`, `namespace`, `projectSlug`, `accessMode`,
`visibility`, `baseline`, `viewerRole` and `manageGrants`. Baseline fields describe
organization-member, namespace-owner and public access. Grant details remain
available through `grants()`. `access()` requires Project read access; changing
the policy requires current Project ADMIN. Unauthorized/unsupported requests use
the server's errors rather than a weaker fallback. The SDK checks that successful
responses identify the requested resource and confirm the requested mode.

Restricting the Project changes only Project-derived access. It does not revoke
independent Note, Artifact, attachment or resource shares. A resource may remain
accessible through another Project or its own owner/visibility/share contract.
Removing a single user/team grant also does not remove other access paths.

The SDK sends one request and never automatically retries policy writes. A
transport timeout can follow an accepted change; inspect the policy before
resubmitting. An invalid response is not a confirmed policy update. `ProjectAccessError`
exposes stable HTTP/error codes without copying arbitrary server-body details.
Existing role/ownership, last-administrator and team-membership checks remain
server responsibilities.

See the [shared API design](https://docs.dreamlake.ai/notes/api/) for named
argument, identity and outcome conventions across CLI, TypeScript and tools.

### Create restricted from the start

Use atomic creation for confidential new Projects, rather than creating a
workspace-readable Project and restricting it afterward:

```python
project = client.projects.create(
    namespace="acme",
    name="Confidential research",
    slug="confidential-research",
    access_mode="restricted",
    visibility="private",
)
project_id = project["id"]
```

The named helper accepts optional `slug` and `description`; `name` is the display
name. `workspace` and `private` are its defaults. Before restricted creation, the
SDK reads the Project catalog's explicit `accessPolicy` capability and requires
API version 1, both modes and `atomicCreate: true`. An old/unsupported server gets
no creation request, so it cannot ignore the field and expose the Project. The
SDK then sends one POST containing the intended mode and checks the returned ID,
slug, visibility and mode. There is no create-then-PATCH fallback.

If creation returns an unconfirmed receipt, `PROJECT_CREATE_UNCONFIRMED` exposes
`project_id` when available and `outcome="unknown"`. Preserve that ID and inspect
the server before deciding what to do; do not create a replacement or delete it
automatically. Existing creation paths retain their own compatibility behavior.

## Whole Project transfer

Typed Artifact/Env transfer plans require SDK **0.31.0+** and a compatible
backend. SDK 0.30.0 supports Note-only resource manifests and direct Project
files, and rejects mixed Artifact/Env plans. Verify the installed package
version before using the expanded support.

`client.projects.transfer_plan(namespace, project)` resolves the Project name
and reads the server's owner-only complete inventory. `client.projects.move`
previews the Project plus all attached, source-namespace-owned Notes, Artifacts
and Envs in one ownership journal request when the updated client and backend
are present. With the compatible file-transfer backend this also
includes direct ordinary Project files, images and videos; SDK 0.30.0 needs no
upgrade for this server capability. Other byte-backed leaves, such as JSON or
audio, qualify with a supported storage binding; episode content remains
unsupported. Direct file/image/video Nodes retain all storage metadata, including
`s3Key` and `bssId`, without object checks, BSS migration or a new media byte-size
cap. A missing object remains missing and does not block the ownership change. Project file Node IDs and folder layout stay
stable, as do physical storage keys; media bytes are not copied. New requests
check the current owner. Previously issued read/upload URLs retain their existing
expiry and can still access or modify the underlying object until then; transfer
does not immediately revoke those capabilities. Requires SDK 0.30.0 and an enabled
whole-Project server adapter; SDK 0.29.0 and earlier lack these methods. Check the plan's
`executable` field and preview blockers independently. Installing the SDK does
not enable backend writer, storage or RTC capabilities.

```python
import json
from pathlib import Path
from dreamlake import DreamLakeClient

client = DreamLakeClient()
review = client.projects.move("personal", "research", "acme", dry_run=True)
Path("project-move-review.json").write_text(json.dumps(review, indent=2))
# Review the full manifest, blockers and server access/external-filing inventory.
# If review["ready"] is true and the user has explicitly authorized submission:
# result = client.projects.move("personal", "research", "acme",
#     dry_run=False, confirm=True, reviewed=review)
# The recipient owner reviews and explicitly accepts result["operation"]["id"].
```

Default policy is private with old access revoked, and
`preserve_external_filings=False`: external Project references to the moved
Notes detach by default, without deleting Notes or their filings inside the
transferred Project. Explicit `preserve_external_filings=True` requests retention
of reviewed **Note** references only. Artifact/Env attachments in other Projects
always detach. `preview["summary"]["detachedOtherFilings"]` counts those removals;
`detachedFilings` counts all removals. Detaching does not delete resources. Saved review replay preserves that exact
policy, including an explicit retention choice. Opt-in `preserve_access=True` requests only
eligible access bound by the source owner's preview and recipient acceptance.
The complete review is returned unchanged, including preservation and external
filing evidence; inspect it before submitting. Project ADMIN does not confer
transfer authority. Cross-user transfers return pending requests and never
accept on behalf of another person. Recipient inbox/status returns accepted
policy, summary counts and the preview hash; it does not expose the source
owner's full manifest or access/filing inventory.

For a caller who owns the destination, `accept_if_owner=True` explicitly
requests acceptance after server authority verification; `wait=True` polls
status for at most `wait_timeout` seconds (0–60, default 30). A timeout returns
the original operation and `timedOut=True`; no mutation is retried. Alternatively
submit with `confirm=True`, `dry_run=False`, the exact reviewed `preview_hash`
and a stable `idempotency_key`. Persist the review before submission and reuse
`reviewed=review` after ambiguous outcomes, including when the source Project
has already moved. Do not replace the key or simulate transfer with copy/delete.

The expanded backend and updated client accept typed `project`, `note`,
`artifact` and `env` manifest references. `resourceNames` covers every non-root
resource and `noteNames` is the exact Note subset; malformed, partial, duplicate
or unknown manifests fail closed. Returned `resourceCounts` includes all four
types. Older Note-only server plans remain compatible.

Canonical names stay unchanged. Use `destination_name` for an explicit Project
rename or `destination_names={NOTE_ID: NEW_NAME}` for individual Note collisions,
then review a new preview. Artifact/Env canonical names cannot be overridden;
their destination collisions block the whole request. Read the server-advertised
`limits`: the expanded backend permits **512 manifest resources including the
Project**, up to 511 Notes if no Artifacts or Envs consume slots, and **4,096 tree
nodes**. Ordinary Project files count toward tree nodes, not additional manifest
resources. The aggregate **5,000-row inventory**, **8 MiB execution ledger** and
**12 MiB total operation** budgets can block smaller Projects. Note/NoteFile
storage verification retains its **2,000-object / 512 MiB** limit, separately
from ordinary Project files. Oversized plans fail as a whole; the SDK never
truncates or splits the plan, and 511 Notes is a ceiling, not a guaranteed fit.
Older servers retain their 20-Note / 200-node limits. SDK 0.30.0 follows the server
plan limits and needs no runtime upgrade for this capacity increase with
Note-only manifests; typed Artifact/Env plans require the updated client above.

Source, Annotation, Workflow, Task, Dataset and Episode transfers remain
unsupported. Foreign-owned resources and active deletion work return structured
422 blockers rather than partial plans. Source bindings
are retained by the existing design and are not migrated by this feature.

Errors in this ownership flow use `OwnershipError`; malformed or partial
plans fail closed. SDK 0.30.0 exposes the error code/status, not the response
body; use the application transfer dialog or an authorized raw API request to
inspect the server's itemized `error.details.blockers`. `client.ownership.transfer_plan(namespace, project_id)` also
exposes the scoped GET endpoint directly. Existing generic ownership methods
remain available for exact saved requests and operation decisions.
