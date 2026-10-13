# Project grants and attachments

Development support; requires the companion unreleased server changes. Project
visibility alone does not expose private resources. Explicit grants include
current and future attached content; independent resource sharing remains valid.

```python
from dreamlake.api import DreamLakeClient

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
Project ADMIN never changes ownership or grants credential/share management.

The SDK sends exact scoped requests without automatic mutation retries or copied
share links. `ProjectAccessError` reports the server denial and HTTP status.
