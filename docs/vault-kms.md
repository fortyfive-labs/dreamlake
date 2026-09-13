# Prefix KMS policies

**Python 0.14.0 release candidate; matching backend required.** Managed KMS is already the default. Personal owners can inspect operator-approved key references and activate them for an empty prefix. This does not onboard arbitrary cloud keys, grant cloud permissions, or implicitly migrate existing data. The explicit migration flow is described below; the SDK never prompts.

::::{tab-set}

:::{tab-item} Python

```python
client.vault.kms.show(prefix="alice/research")
client.vault.kms.preview(prefix="alice/research", key_ref="research-key")
# Persist account/server plus this exact tuple before sending it.
client.vault.kms.activate(prefix="alice/research", key_ref="research-key", request_id="research-kms-001")
client.vault.kms.status(request_id="research-kms-001")
```

:::

:::{tab-item} CLI

```shell
dreamlake vault -p alice/research kms show
dreamlake vault -p alice/research kms preview --key research-key
dreamlake vault -p alice/research kms activate --key research-key --request-id research-kms-001
dreamlake vault kms status research-kms-001
```

:::
::::

Preview is metadata-only, does not probe KMS and does not reserve the prefix. Activation verifies the approved key and commits only when there are no entries (including retired/expired entries), retained write/HOTP receipts, or overlapping ancestor/descendant policies. Parent boundaries dominate; path segments must match exactly. Scoped access keys cannot manage policies.

`VaultWriteError` preserves `request_id` after an uncertain activation. Inspect `status()` and retry only the same account/server/prefix/key reference/request ID. A missing receipt does not prove an in-flight operation will never commit. Returned metadata omits unsolicited fields and key ARNs.

Populated-prefix migration is an unreleased candidate described below; customer key provisioning remains separate work. Never remove historical KMS access merely because a new default/reference exists: retained entries, recovery receipts and backups may still depend on it. [Development plan](https://docs.dreamlake.ai/dev/plans/vault-prefix-kms/); [tabbed CLI/Python guide](https://cli.dreamlake.ai/vault-kms/).

## Python API

```{autoclass} dreamlake.vault_kms.VaultKms
:members:
```

## Populated prefixes (unreleased)

Use an immutable request ID for start and each recovery attempt. `resume` processes at most 1–100 ciphertext records per call and never launches an implicit background loop. Records include retained retired entries and write/HOTP receipts whose source entry may have been purged.

::::{tab-set}

:::{tab-item} Python

```python
client.vault.kms.preview(prefix="alice/research", key_ref="research-key")
client.vault.kms.migrate(prefix="alice/research", key_ref="research-key", request_id="research-move-001")
client.vault.kms.resume(request_id="research-move-001", limit=50, prefix="alice/research")
client.vault.kms.status(request_id="research-move-001")
```

:::

:::{tab-item} CLI

```shell
dreamlake vault -p alice/research kms preview --key research-key
dreamlake vault -p alice/research kms migrate --key research-key --request-id research-move-001
dreamlake vault -p alice/research kms resume research-move-001 --limit 50
dreamlake vault kms status research-move-001
```

:::
::::

Repeat bounded resume explicitly until completed. Logical credential revisions and HOTP counters stay unchanged. A lost response raises `VaultWriteError` with the original ID; it is not proof of rollback. `managed` is the explicit operator default target, and reversal requires a new operation after completion. No arbitrary key ARN or provider fallback is accepted.

Completion covers the active database, not retained backups or remote credential revocation. Keep historical keys until actual backup/recovery retention permits retirement. The candidate uses one configured provider: AWS keys stay in its configured region, while GCP accepts operator-allowlisted CryptoKey locations without cross-location live acceptance; customer grants, cross-provider routing and GCP live setup remain required. All writing backend instances must enforce epochs before migration is exposed. These APIs are unreleased source, not deployed behavior.

Optional `prefix=` on resume is asserted by the server against the original operation before any mutation. Omit it for ID-only recovery. CLI `--prefix` behaves identically; status also rejects a mismatched explicit prefix.

Migration routes require the operator flag `DREAMLAKE_VAULT_KMS_MIGRATION_ENABLED=true`, default off. Enable it only after every backend writer is verified epoch-aware. Disabling it preserves policies and completed progress; it does not authorize rolling back to old writers.
