# Dreamlake Python SDK

A simple and flexible Python SDK for ML experiment tracking and data storage.

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

```bash
uv add dreamlake
```

</td>
<td>

```bash
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

```bash
uv sync --extra dev
```

</td>
<td>

```bash
pip install -e ".[dev]"
```

</td>
</tr>
</table>

This installs:
- `pytest>=8.0.0` - Testing framework
- `pytest-asyncio>=0.23.0` - Async test support
- `mkdocs>=1.5.0` - Documentation builder
- `mkdocs-material>=9.5.0` - Material theme for MkDocs
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

```bash
uv run pytest
```

</td>
<td>

```bash
pytest
```

</td>
</tr>
</table>

### Building Documentation

<table>
<tr>
<td>Build docs</td>
<td>Preview docs locally</td>
</tr>
<tr>
<td>

```bash
uv run docs
```

</td>
<td>

```bash
uv run preview
```

</td>
</tr>
</table>

The `preview` command starts a local server at `http://127.0.0.1:8000` for viewing documentation.

For maintainers, to build and publish a new release: `uv build && uv publish`
