# Dreamlake

A simple and flexible SDK for ML experiment tracking and data storage.

## Features

- **Three Usage Styles**: Decorator, context manager, or direct instantiation
- **Dual Operation Modes**: Remote (API server) or local (filesystem)
- **Auto-creation**: Automatically creates namespace, workspace, and folder hierarchy
- **Upsert Behavior**: Updates existing episodes or creates new ones
- **Simple API**: Minimal configuration, maximum flexibility
- **Time-Based Queries**: MCAP-like API for querying track data by timestamp ranges
- **Multi-Modal Sync**: Timestamp inheritance for synchronizing pose, images, and sensor data

## Installation

<table>
<tr>
<td>Using uv (recommended)</td>
<td>Using pip</td>
</tr>
<tr>
<td>

```shell
uv add dreamlake@0.4.2
```

</td>
<td>

```shell
pip install dreamlake==0.8.0
```

</td>
</tr>
</table>

## CLI (deprecated — use the standalone DreamLake CLI)

The Python CLI bundled in this package is **deprecated**, and this package
no longer installs a `dreamlake` console script. Install the standalone
[DreamLake CLI](https://github.com/dreamlake-ai/dreamlake-cli) instead —
same commands, flags, and env vars, plus environment switching
(`dreamlake env use`):

```shell
curl -fsSL https://dl.dreamlake.ai/install.sh | bash
```

`upload`, `download`, `list`, `create`, `delete`, `update` and `vectorize`
have been **removed** from this package; they live in the standalone CLI.
What is still here, reachable through `python -m dreamlake.cli`:

| Command | Status |
| --- | --- |
| `artifact append-local`, `workflow append-local` | **not deprecated** — see below |
| `artifact push\|list\|delete\|restore` | deprecated; the standalone CLI has these |
| `workflow push\|list` | deprecated; the standalone CLI has these |
| `source create\|collection create\|push` | kept — no standalone-CLI equivalent yet |
| `video upload\|download\|list` | deprecated; the standalone CLI has these |
| `login`, `logout`, `profile` | kept — `login` is the only writer of the token store `artifact push` / `workflow push` read |

### The `append-local` writers are not deprecated

`artifact append-local` and `workflow append-local` are the canonical
DreamDB writers, and **dreamlake-server spawns them as a subprocess** —
`routes/workflows.ts` for workflow apply, `routes/artifacts.ts` for
artifact apply. Their stdout is a machine contract: exactly one JSON line,
which the server parses as the last non-empty line of stdout. Errors go to
stdout too, as JSON. Do not reformat either.

```
workflow append-local  →  {"version": N, "meta": {description, stageCount, nodeCount, edgeCount}}
artifact append-local  →  {"version": N}            # no "meta" key — the two are not symmetric
error (either)         →  {"error": "...", "message": "..."}   # exit 1
```

### The wrapper bin

The server spawns `${WORKFLOWS_APPLY_BIN:-dreamlake} workflow append-local …`
and `${ARTIFACTS_APPLY_BIN:-…} artifact append-local …`. The `dreamlake` on
`PATH` is the standalone TS CLI, which has no DreamDB writer, and
`pip install dreamlake` deliberately installs no bin of its own. So point
both env vars at the wrapper shipped in this repo:

```shell
export WORKFLOWS_APPLY_BIN=/path/to/dreamlake-py/bin/dreamlake-append-local
export ARTIFACTS_APPLY_BIN=/path/to/dreamlake-py/bin/dreamlake-append-local
```

It is a one-line `exec python3 -m dreamlake.cli "$@"` and must stay one —
anything the wrapper prints would corrupt the JSON contract above. It is
not registered in `[project.scripts]`, on purpose: the `dreamlake` name on
`PATH` belongs to the standalone CLI.

## Quick Start

### Remote Mode (with API Server)

```python
from dreamlake import Episode

with Episode(
    name="my-experiment",
    workspace="my-workspace",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    api_key="your-jwt-token"
) as episode:
    print(f"Episode ID: {episode.id}")
```

### Local Mode (Filesystem)

```python
from dreamlake import Episode

with Episode(
    name="my-experiment",
    workspace="my-workspace",
    local_path=".dreamlake"
) as episode:
    pass  # Your code here
```

See [examples/](examples/) for more complete examples.

## Development Setup

### Installing Dev Dependencies

To contribute to Dreamlake or run tests, install the development dependencies:

<table>
<tr>
<td>Using uv (recommended)</td>
<td>Using pip</td>
</tr>
<tr>
<td>

```shell
uv sync --extra dev
```

</td>
<td>

```shell
pip install -e ".[dev]"
```

</td>
</tr>
</table>

This installs:
- `pytest>=8.0.0` - Testing framework
- `pytest-asyncio>=0.23.0` - Async test support
- `sphinx>=7.2.0` - Documentation builder
- `sphinx-rtd-theme>=2.0.0` - Read the Docs theme
- `sphinx-autobuild>=2024.0.0` - Live preview for documentation
- `myst-parser>=2.0.0` - Markdown support for Sphinx
- `ruff>=0.3.0` - Linter and formatter
- `mypy>=1.9.0` - Type checker

### Running Tests

<table>
<tr>
<td>Using uv</td>
<td>Using pytest directly</td>
</tr>
<tr>
<td>

```shell
uv run pytest
```

</td>
<td>

```shell
pytest
```

</td>
</tr>
</table>

### Building Documentation

Documentation is built using Sphinx with Read the Docs theme.

<table>
<tr>
<td>Build docs</td>
<td>Live preview</td>
<td>Clean build</td>
</tr>
<tr>
<td>

```shell
uv run python -m sphinx -b html docs docs/_build/html
```

</td>
<td>

```shell
uv run sphinx-autobuild docs docs/_build/html
```

</td>
<td>

```shell
rm -rf docs/_build
```

</td>
</tr>
</table>

The live preview command starts a local server and automatically rebuilds when files change.

Alternatively, you can use the Makefile from within the docs directory:

```shell
cd docs
make html          # Build HTML documentation
make clean         # Clean build files
```

For maintainers, to build and publish a new release: `uv build && uv publish`
