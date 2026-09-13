# Prefix KMS policies

**Unreleased; matching backend required.** Managed KMS is already the default. Personal owners can inspect operator-approved key references and activate them for an empty prefix. This does not onboard arbitrary cloud keys, grant cloud permissions, or migrate existing data. The SDK never prompts.

```python
client.vault.kms.show(prefix="alice/research")
client.vault.kms.preview(prefix="alice/research", key_ref="research-key")
# Persist account/server plus this exact tuple before sending it.
client.vault.kms.activate(prefix="alice/research", key_ref="research-key", request_id="research-kms-001")
client.vault.kms.status(request_id="research-kms-001")
```

```shell
dreamlake vault -p alice/research kms show
dreamlake vault -p alice/research kms preview --key research-key
dreamlake vault -p alice/research kms activate --key research-key --request-id research-kms-001
dreamlake vault kms status research-kms-001
```

Preview is metadata-only, does not probe KMS and does not reserve the prefix. Activation verifies the approved key and commits only when there are no entries (including retired/expired entries), retained write/HOTP receipts, or overlapping ancestor/descendant policies. Parent boundaries dominate; path segments must match exactly. Scoped access keys cannot manage policies.

`VaultWriteError` preserves `request_id` after an uncertain activation. Inspect `status()` and retry only the same account/server/prefix/key reference/request ID. A missing receipt does not prove an in-flight operation will never commit. Returned metadata omits unsolicited fields and key ARNs.

Populated-prefix migration and customer key provisioning remain separate work. Never remove historical KMS access merely because a new default/reference exists: retained entries, recovery receipts and backups may still depend on it. [Development plan](https://docs.dreamlake.ai/dev/plans/vault-prefix-kms/); [tabbed CLI/Python guide](https://cli.dreamlake.ai/vault-kms/).

## Python API

```{autoclass} dreamlake.vault_kms.VaultKms
:members:
```
