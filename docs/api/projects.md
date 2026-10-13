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
