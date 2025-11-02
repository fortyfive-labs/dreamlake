# Dreamlake

A simple and flexible SDK for ML experiment tracking and data storage.

## Features

- **Three Usage Styles**: Decorator, context manager, or direct instantiation
- **Dual Operation Modes**: Remote (API server) or local (filesystem)
- **Auto-creation**: Automatically creates namespace, workspace, and folder hierarchy
- **Upsert Behavior**: Updates existing sessions or creates new ones
- **Simple API**: Minimal configuration, maximum flexibility

## Installation

<table>
<tr>
<td>Using uv (recommended)</td>
<td>Using pip</td>
</tr>
<tr>
<td>

```shell
uv add dreamlake
```

</td>
<td>

```shell
pip install dreamlake
```

</td>
</tr>
</table>

## Quick Start

### Remote Mode (with API Server)

```python
from dreamlake import Session

with Session(
    name="my-experiment",
    workspace="my-workspace",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    api_key="your-jwt-token"
) as session:
    print(f"Session ID: {session.id}")
```

### Local Mode (Filesystem)

```python
from dreamlake import Session

with Session(
    name="my-experiment",
    workspace="my-workspace",
    local_path=".dreamlake"
) as session:
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
