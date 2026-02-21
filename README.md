# Dreamlake Python SDK

A simple and flexible Python SDK for ML experiment tracking and data storage.

**Looking for the web app?** See [dreamlake-ai](https://github.com/vuer-ai/dreamlake-ai).

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

For maintainers, to build and publish a new release: `uv build && uv publish`
