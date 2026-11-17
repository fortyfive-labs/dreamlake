# Connections

A **Connection** is a credential DreamLake holds for a namespace to reach
another system — an S3-compatible bucket, a Hugging Face account, Dropbox.
Sources read their external layer through one. `client.connections` manages
them. It is not a Lakeshore connection, and it is not how a computer joins
DreamLake (host enrollment).

Only the namespace's credential managers (a personal namespace's owner, an
organization's owners) can use these calls, with a signed-in user token.
Everyone else — and any unknown id — gets `ConnectionNotFound`. Machine and
service-account tokens are refused, and agent service accounts cannot be
granted a Connection.

## Discover what this server accepts

Connector kinds, auth kinds, inputs and product or profile choices are declared
by the server, per deployment:

```python
from dreamlake import DreamLakeClient

client = DreamLakeClient()
for connector in client.connections.connectors("my-team"):
    for auth in connector["auth"]:
        print(connector["kind"], auth["authKind"], auth["mode"],
              [(f["name"], f["target"]) for f in auth["create"]])
```

`mode` is `fields` (typed inputs), `oauth` (browser sign-in) or `none` (no
credential). Each field's `target` says whether it belongs in `credential` or
`private_config`; `requiredWhen` and `options` carry conditional requirements
and choices. S3 offers an `access-key` auth kind (access key id, secret,
optional session token) and, for the `aws` profile only, a `role` auth kind:
`roleArn` goes in `private_config` and `externalId` in `credential`. The role's
trust policy must already allow DreamLake's AWS identity with an
`sts:ExternalId` condition equal to that external id; get that identity from
whoever operates your DreamLake deployment. `profileId` options are the
profiles this deployment configured.

## Create, inspect and change

```python
import json, uuid

credential = json.load(open("credential.json"))   # never hard-code a secret
created = client.connections.create(
    "my-team", label="Team bucket", connector_kind="s3", auth_kind="access-key",
    credential=credential, private_config={"profileId": "aws", "region": "us-east-1"},
    idempotency_key=str(uuid.uuid4()),
)
client.connections.check("my-team", created["id"])        # available | unavailable | unverified
page = client.connections.list("my-team", lifecycle="active")
```

A refused credential creates no Connection. `check` reports `available` when the
provider accepted the credential at the Connection's endpoint (no bucket is
implied) and `unverified` when that way in has no check without a target;
bucket and prefix access is checked by `import_external`.

`rename`, `reconnect`, `enable`, `disable` and `delete` take the
`expected_settings_revision` you last read and an `idempotency_key`. A stale
revision raises `ConnectionConflict`; re-read and decide again rather than
retrying with a fresher number. Reuse the same key only to retry the identical
request after an uncertain result. `delete` is refused with `CONNECTION_IN_USE`
while `affected_sources` lists a Source using it.

Errors are `ConnectionApiError` subclasses (`ConnectionNotFound`,
`ConnectionConflict`, `ConnectionInvalid`, `ConnectionUnavailable`) with the
server's `code`, `status`, `retryable`, `request_id` and `details`. Neither
errors nor return values contain a credential you passed.

## OAuth and browser credential entry

`oauth_begin` starts provider sign-in for an `oauth` auth kind; open the
returned `authorizationUrl`, then poll `oauth_status(operation_id)`.

`entry_begin` hands credential entry to the signed-in DreamLake web form, for a
`fields` auth kind, when the secret should not pass through your code:

```python
entry = client.connections.entry_begin(
    "my-team", intent="create", connector_kind="s3", auth_kind="access-key",
    suggested_label="Team bucket")
print("Open:", entry["url"])            # opaque id only; yours, once, until expiresAt
# ...after the person finishes in the browser:
done = client.connections.entry_status("my-team", entry["operationId"])
if done["state"] == "succeeded":
    connection_id = done["resultConnectionId"]
```

The connector and auth kind are fixed by the entry; the label is a suggestion.
`intent="reconnect"` with `connection_id` replaces an existing credential at the
revision the entry started from. `entry_cancel` closes a pending entry.

## Use it from a Source

```python
source = client.sources.import_external(
    "my-team", connector_kind="s3", connection_id=connection_id,
    target={"bucket": "team-data", "prefix": "episodes/"},
    idempotency_key=str(uuid.uuid4()))
```

`set_external` and `remove_external` attach, replace or detach a Source's
external layer with its `expected_composition_revision`. The Connection decides
the endpoint profile; the target names only the bucket and prefix.
