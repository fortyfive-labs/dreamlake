# Noninteractive host SSH acceptance

The SDK promises no terminal prompts. OpenSSH's target `BatchMode=yes` does not
apply to the ProxyJump subprocess. An SDK call made from a terminal could therefore
prompt for the jump password even though the target was noninteractive.

The fix starts SSH in a separate session and sets `SSH_ASKPASS_REQUIRE=never`,
which the jump process inherits. Target and jump access must use a configured key
or agent. Password-based interactive enrollment remains supported by the CLI.

## Local boundary and existing tests

```shell
uv run python scripts/test_host_noninteractive_ssh.py
uv run pytest test/test_hosts.py -q
```

The first script substitutes only the SSH executable with a local process that
reports its session and askpass policy. No SSH connection or API call occurs. It
verifies detachment from the calling session and override of forced askpass. It
removes its temporary executable and restores environment variables afterwards.

## Real OpenSSH password/ProxyJump regression

The companion CLI [PR #72](https://github.com/dreamlake-ai/dreamlake-cli/pull/72)
contains the reusable real SSH fixture and pinned test dependencies. Clone/check
out that test branch separately, install its CLI dependencies, then from the SDK:

```shell
uv run --with paramiko==4.0.0 --with httpx==0.28.1 python \
  /absolute/path/to/dreamlake-cli/scripts/test-host-ssh-password.py \
  --sdk-source "$PWD/src/dreamlake/api/hosts.py"
```

Expected: CLI direct/password-jump probes pass, then the actual SDK helper rejects
password-only ProxyJump without a terminal or GUI prompt. The old SDK reproduced
an unexpected jump password prompt; the fixed helper passes. This uses real
OpenSSH and loopback SSH protocol servers with generated in-memory passwords;
systemd checks are stubbed. It is transport proof, not live host enrollment or a
nymph workload test. No shared services, OS users or existing credentials change.
