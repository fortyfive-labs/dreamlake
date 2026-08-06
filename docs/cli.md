# CLI

The user-facing CLI is the **standalone DreamLake CLI**, not this package:

```bash
curl -fsSL https://dl.dreamlake.ai/install.sh | bash
```

`upload`, `download`, `list`, `create`, `delete`, `update` and `vectorize`
have been removed from the Python package — install the standalone CLI and
run them there. This package installs no `dreamlake` console script.

What remains in the Python package is reachable only as a module:

```bash
python -m dreamlake.cli <command> ...
```

## `append-local` — the canonical DreamDB writers

These two subcommands are **not deprecated**. `dreamlake-server` spawns them
as a subprocess to apply a workflow or an artifact into DreamDB, so their
input and output are a machine contract.

```bash
# Workflow apply. Reads a WorkflowSpec v1 JSON document on stdin.
python -m dreamlake.cli workflow append-local \
    --backend <s3-backend-url> --name <workflow-name> [--ref <dataset-ref>]

# Artifact apply. Reads the artifact content on stdin.
python -m dreamlake.cli artifact append-local \
    --backend <s3-backend-url> --id <artifactId> [--ref <dataset-ref>]
```

**Output contract.** Success is exit 0 and exactly one JSON line on stdout.
Failure is exit 1 and one JSON line on stdout as well — errors are *not*
routed to stderr, because the server parses the last non-empty line of
stdout and treats stderr only as a fallback blob.

```jsonc
// workflow append-local, success
{"version": 3, "meta": {"description": "", "stageCount": 2, "nodeCount": 5, "edgeCount": 4}}

// artifact append-local, success — note there is no "meta" key.
// The two writers are not symmetric; code that assumes they are is wrong.
{"version": 7}

// either writer, failure (exit 1)
{"error": "validation", "problems": ["version: must be the literal 1", "..."]}
{"error": "open_failed", "message": "..."}
```

Do not reformat either writer's output, and do not add prints around it.

### The wrapper bin

The server spawns `${WORKFLOWS_APPLY_BIN:-dreamlake} workflow append-local …`
and `${ARTIFACTS_APPLY_BIN:-…} artifact append-local …`. The `dreamlake` on
`PATH` is the standalone TS CLI, which has no DreamDB writer, and
`pip install dreamlake` deliberately installs no bin. Point both env vars at
the wrapper shipped in this repo:

```bash
export WORKFLOWS_APPLY_BIN=/path/to/dreamlake-py/bin/dreamlake-append-local
export ARTIFACTS_APPLY_BIN=/path/to/dreamlake-py/bin/dreamlake-append-local
```

`bin/dreamlake-append-local` is a pure `exec python3 -m dreamlake.cli "$@"`
and must stay that way: anything it prints lands on the child's stdout and
corrupts the JSON contract above.

Credentials come from the environment the server hands the child — the
`AWS_*` variables — so the writers need no login.

## `artifact` and `workflow` — deprecated, still present

The standalone CLI has both groups; prefer it. These stay in the Python
package because they share their validation and append code with the
`append-local` writers above.

```bash
python -m dreamlake.cli artifact push ./dashboard.html --title "Q1 Dashboard"
python -m dreamlake.cli artifact list
python -m dreamlake.cli artifact delete <artifactId>
python -m dreamlake.cli artifact restore <artifactId>

python -m dreamlake.cli workflow push ./spec.json
python -m dreamlake.cli workflow list
```

`push` and `list` need a token: either `DREAMLAKE_API_KEY`, or a
`python -m dreamlake.cli login` that writes the local token store.

## `source` — managed DreamDB video sources

Kept because the standalone CLI has no `source` group and the write path
(CMAF slicing) lives only in the Python `dreamdb` SDK.

```bash
python -m dreamlake.cli source create <name> ...
python -m dreamlake.cli source collection create <name> --source <src> [--preset video | --schema s.json]
python -m dreamlake.cli source push ...
```

## `video` — deprecated

Superseded by `dreamlake video` in the standalone CLI.

```bash
python -m dreamlake.cli video upload ./demo.mp4 --user alice --project robotics
python -m dreamlake.cli video download <id> --output ./my_video.mp4
python -m dreamlake.cli video list --user alice --project robotics
```

## Environment variables

```bash
export DREAMLAKE_REMOTE="http://localhost:10334"   # API server
export DREAMLAKE_API_KEY="your-jwt-token"
export DREAMLAKE_BSS_URL="http://localhost:10234"  # video commands
export DREAMLAKE_BSS_TOKEN="your-bss-jwt-token"
```

`--debug` as a global flag points `DREAMLAKE_REMOTE` and `DREAMLAKE_BSS_URL`
at those two localhost defaults.
