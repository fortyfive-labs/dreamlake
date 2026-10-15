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
or unknown manifests fail closed. SDK 0.31.0 returns `resourceCounts` for those
four types; the [unreleased expansion below](#annotation-and-source-expansion-unreleased)
reports all six supported types. Older Note-only server plans remain compatible.

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

In SDK 0.31.0, Source, Annotation, Workflow, Task, Dataset and Episode
transfers remain unsupported; the unreleased expansion below adds Annotation and
Source manifest members only. Foreign-owned resources and active deletion work
return structured 422 blockers rather than partial plans. Source bindings
are retained by the existing design and are not migrated by this feature.

(annotation-and-source-expansion-unreleased)=

### Annotation and Source expansion (UNRELEASED)

**Everything in this subsection is unreleased.** It requires a forthcoming SDK
release newer than 0.31.0 together with a compatible backend; neither is
published yet. The 0.31.0 Artifact/Env facts above are unchanged, and SDK
0.31.0 and older clients reject plans containing the new manifest types.

The server plan `manifest` and complete `resourceNames` additionally accept
typed `annotation` and whole-`source` references, so the supported member set
becomes `project`, `note`, `artifact`, `env`, `annotation` and `source`. The
root remains the single `project` reference. All existing fail-closed rules
apply unchanged: missing, partial, duplicate or mismatched names, wrong root,
duplicate references, unknown reference types (including `workflow`, `task` and
`dataset`), partial manifests and malformed numeric limits reject the plan, and
the numeric `maxNotes`, `maxTreeNodes` and `maxResources` limits keep their
existing meaning. A Project whose members are only Annotations and Sources —
without any Note — is a valid complete plan. There is no standalone Source,
Annotation, Task or Dataset transfer API.

Task rows and Project-owned Dataset rows — the Prisma Dataset/Bindr containers
attached to the Project — and their task events are dependent rows rather than
manifest members: they ride with the Project and are reported only as counts
through the optional `managedCounts` object `{datasets, tasks, taskEvents}` on
the plan and on `preview["summary"]`. The separate LabelDataset has no Project
relationship and remains entirely outside this transfer; it is never counted or
moved. When the object is present, each of the
three counts is required and must be a nonnegative safe integer; booleans and
other malformed values fail closed on both the plan and the preview. Legacy
servers omit the object entirely and remain compatible.
`summary["includedCount"]` always equals the manifest length and never counts
dependent rows.

Source members are pure-managed. The backend alone decides Source eligibility;
unsupported or mixed/external Sources may instead return structured server
blockers. When a plan exists, inspect its `executable` field and the preview
blockers. The SDK never infers eligibility and, as everywhere in this flow,
parses and submits server metadata only — no file or payload bytes are moved
or copied.
Moved Source members keep their original root binding/reference IDs; generic
SourceBinding migration remains deferred, and external source bindings detach
under the existing same-namespace invariants.

`preserve_external_filings=True` continues to retain reviewed **Note**
references only. All other external filings — Artifact, Env and now also
Annotation and Source attachments in other Projects — detach, and the backend
can count them in `preview["summary"]["detachedOtherFilings"]`. Destination
renames stay Note-only: `destination_names` rejects Annotation and Source IDs
exactly as it rejects Artifact and Env IDs. The review's `resourceCounts`
reports all six supported types, with zero for types a plan does not include.

Unknown additive fields returned by a newer server — on the plan, in the
preview summary, or inside `managedCounts` — are preserved in the saved review
and replayed unchanged. The exact saved-review JSON replay and idempotency
policy is unchanged, and the SDK never regenerates a manifest. Read the counts
from the review's existing dictionaries: `review["plan"]["managedCounts"]`
(plan-time inventory) and `review["preview"]["summary"]["managedCounts"]`
(previewed scope, also shown to the destination inbox).

Errors in this ownership flow use `OwnershipError`; malformed or partial
plans fail closed. SDK 0.30.0 exposes the error code/status, not the response
body; use the application transfer dialog or an authorized raw API request to
inspect the server's itemized `error.details.blockers`. `client.ownership.transfer_plan(namespace, project_id)` also
exposes the scoped GET endpoint directly. Existing generic ownership methods
remain available for exact saved requests and operation decisions.
