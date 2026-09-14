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

## Provider checks and placed runs (unreleased)

Requires the server API in [workspace PR #389](https://github.com/dreamlake-ai/dreamlake-workspace/pull/389)
and a tracked Slurm worker. These client additions are not released yet.

```python
provider_id = "aaaaaaaaaaaaaaaaaaaaaaaa"  # Replace with your provider ID.
association_id = "bbbbbbbbbbbbbbbbbbbbbbbb"  # Your owned association.
probe = client.providers.check(
    "team", provider_id, association_id=association_id,
    association_revision=1, request_id="probe-001",
)
client.runs.wait("team", probe["id"])
status = client.providers.status("team", provider_id)
# Inspect this association's readiness before submitting.
run = client.runs.submit(
    "team/lab/host", kind="uv-run", argv=["train.py"], include=["train.py"],
    request_id="train-001",
    placement={"providerId": provider_id, "associationId": association_id,
               "associationRevision": 1},
    resources={"cpus": 4, "memoryMib": 8192, "gpus": 0},
)
```

A check explicitly consumes a small Slurm allocation (1 CPU, 512 MiB, no GPU,
120-second CP timeout). `runs.status`, `runs.logs`, `runs.wait` and `runs.cancel`
operate on its returned run ID. Waiting does not imply success; inspect the returned
status. Readiness must be successful, current and from the same association/worker.
It expires after 15 minutes and proves submission/environment access, not capacity
or GPU isolation. A new check supersedes previous evidence.

After a lost response, repeat identical input with the same request ID. Provider
checks use run receipts, not provider-operation receipts. There are no automatic
write retries. Resources are optional and default to 1 CPU, 512 MiB and zero GPUs;
resource fields require placement, and placement supports `uv-run` only. Changed
resource values under an accepted request ID conflict. Paired real-server and
live-worker acceptance remain open.
