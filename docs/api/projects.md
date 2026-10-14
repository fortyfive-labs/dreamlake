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

## Whole Project and Notes transfer (SDK 0.30.0)

`client.projects.transfer_plan(namespace, project)` resolves the Project name
and reads the server's owner-only complete inventory. `client.projects.move`
previews the Project plus all attached, source-namespace-owned Notes in one
ownership journal request. With the compatible file-transfer backend this also
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
of the reviewed external references. Saved review replay preserves that exact
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

Names stay unchanged. Use `destination_name` for an explicit Project rename
or `destination_names={NOTE_ID: NEW_NAME}` for individual Note collisions, then
review a new preview. A compatible larger-capacity backend advertises up to
**512 manifest resources** (one Project plus at most 511 Notes) and **4,096 tree
nodes**. Ordinary Project files count toward tree nodes, not additional manifest
resources. The aggregate **5,000-row inventory**, **8 MiB execution ledger** and
**12 MiB total operation** budgets can block a plan before those count limits.
Note/NoteFile storage verification retains its **2,000-object / 512 MiB** limit,
separate from ordinary Project files. Oversized plans fail as a whole; 511 Notes
is a ceiling, not a guaranteed fit. Older servers retain their 20-Note / 200-node
limits. SDK 0.30.0 follows the server plan limits and needs no runtime upgrade for
this capacity increase.

Episode content remains unsupported. Unsupported attachments, foreign-owned Notes
and active deletion work return structured 422 blockers rather than partial plans.
Source bindings
are retained by the existing design and are not migrated by this feature.

Errors in this ownership flow use `OwnershipError`; malformed or partial
plans fail closed. SDK 0.30.0 exposes the error code/status, not the response
body; use the application transfer dialog or an authorized raw API request to
inspect the server's itemized `error.details.blockers`. `client.ownership.transfer_plan(namespace, project_id)` also
exposes the scoped GET endpoint directly. Existing generic ownership methods
remain available for exact saved requests and operation decisions.
