# Provider registrations

Use `client.providers` to store provider configuration and associate your Slurm enrollment. This API requires a server with [workspace PR #321](https://github.com/dreamlake-ai/dreamlake-workspace/pull/321) and was added in Python package 0.11.0. The corresponding CLI commands are in 0.14.0.

## Register and associate

Replace the namespace, cluster, partition and paths with your values. Credential references name Vault entries; do not include credential contents in declarations.

```python
from dreamlake import DreamLakeClient

client = DreamLakeClient()
declaration = {
    "version": 1,
    "name": "research-slurm",
    "kind": "slurm-ssh",
    "config": {
        "cluster": "research",
        "partitions": ["cpu"],
        "credentialRef": {"namespace": "team", "entryName": "research-ssh"},
    },
}
registration = client.providers.register(
    "team", declaration=declaration, request_id="research-register-001"
)
provider_id = registration["resource"]["id"]
association = client.providers.associate(
    "team", provider_id,
    request_id="research-associate-001",
    association={
        "providerRevision": registration["resource"]["revision"],
        "enrollmentId": "0123456789abcdef01234567",  # Your enrollment ID.
        "runner": {
            "kind": "slurm",
            "partition": "cpu",
            "staging": {
                "submissionRoot": "/export/work/alice",
                "computeRoot": "/work/alice",
            },
            "environment": {
                "uvExecutable": "/opt/tools/uv",
                "pythonExecutable": "/usr/bin/python3",
            },
        },
    },
)
status = client.providers.status("team", provider_id)
```

The enrollment must belong to you in the same namespace. Use the shared directory's submission-host path for `submissionRoot` and its compute-node path for `computeRoot`. Executable paths must be valid on compute nodes. The partition and optional account must be permitted by the provider declaration.

Registration stores metadata; it does not test connectivity, provision machines or submit jobs. Status reports `not_checked`. Kubernetes declarations are accepted, but Kubernetes runner associations are not yet supported.

```python
providers = client.providers.list("team", page=1, page_size=50)
provider = client.providers.get("team", "research-slurm")  # Name or ID.
```

## Recover a missing response

Choose and retain a request ID before each write. If the connection fails, look up the receipt with that ID:

```python
receipt = client.providers.operations.find("team", "research-register-001")
same_receipt = client.providers.operations.get("team", receipt["operationId"])
```

If no receipt exists, repeat the original call with the same payload and request ID. A repeated write returns the original receipt; a changed payload with that ID raises `ProviderConflictError`. There are no automatic write retries. Receipts are historical snapshots visible to their creating user; read current state with `status`.

Errors from `dreamlake.api.providers` include `ProviderAuthorizationError`, `ProviderConflictError`, `ProviderNotFound`, and their base `ProviderError`. API and transport failures carry `request_id`; HTTP failures also carry `status` and, when recognized, `code`. Server error text is not included.

## Retire configuration

Pass the current revision of the association or provider being retired:

```python
client.providers.retire(
    "team", provider_id,
    association_id=association["resource"]["id"],
    revision=association["resource"]["revision"],
    request_id="research-association-retire-001",
)
client.providers.retire(
    "team", provider_id, revision=registration["resource"]["revision"],
    request_id="research-provider-retire-001",
)
```

Retirement changes metadata. It does not stop jobs, remove hosts or delete Vault entries. Retire associations separately when you want their records marked retired. Editing declarations and upgrading legacy provider records are not yet supported.
