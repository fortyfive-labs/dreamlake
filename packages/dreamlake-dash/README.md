# dreamlake-dash

The standalone DreamLake experiment recorder, extracted from the ml-dash API
that was merged into `dreamlake`. It owns the recording implementation:
`Experiment`, `experiment`, `RUN`, metrics, tracks, parameters, files, buffering,
local storage, native project sessions, job-broker credentials and job lifecycle.
The umbrella SDK re-exports these same objects; it does not maintain a second
implementation. Install this package when you only need recording.

```sh
pip install dreamlake-dash
```

```python
from dreamlake_dash import Experiment

# Local recording: no account, server, upload or output capture.
with Experiment(prefix="team/research/smoke", root="./runs").run as run:
    run.params.set(seed=0, learning_rate=0.001)
    run.metrics("train").log(step=0, loss=0.25)
    run.log("Recorded one explicit measurement")
    run.files("reports").save_json({"passed": True}, to="summary.json")
```

Use the existing DreamLake CLI login for native recording:

```python
from dreamlake_dash import Experiment, NativeConnection

connection = NativeConnection.from_login("team", "research")
with Experiment(prefix="team/research/smoke", connection=connection).run as run:
    run.metrics("eval").log(step=0, accuracy=0.95)
```

The connection pins credentials to the selected server. Native jobs continue to
use their existing scoped broker credentials. Parameters, logs, metrics, tracks
and artifacts are sent only when you explicitly call their recording methods.
Nothing automatically captures terminal output or indexes files. Remote
recording requires the existing project permissions and service configuration;
installing this package does not provision a service.

`dash_writer` / `DashRun` retain the historical managed Dash backend. They are
separate from `Experiment`'s native recording routes, and do not migrate a run
between backends. An existing `DreamLakeClient` can still be passed via `client=`;
the standalone default reads the same server-pinned CLI login without importing
the umbrella SDK.

`dreamlake-eval` owns test discovery, definitions and verdicts. Dash records
measurements and artifacts; it does not discover tests or decide their outcomes.

## Compatibility

Existing `from dreamlake import Experiment, RUN` and
`from dreamlake.experiments import ...` continue to work in DreamLake 0.63.0+.
The old private `dreamlake.experiments._native.*` paths alias the canonical
modules too, preserving exception/enum identities and existing test patches.
New code should import from `dreamlake_dash`. This package does not install an
`ml_dash` module or replace a separately installed historical `ml-dash` SDK.

The default install needs only HTTPX, params-proto and Rich. Use
`pip install 'dreamlake-dash[media]'` for NumPy/Pillow file helpers and
`'dreamlake-dash[video]'` for video encoding; PyTorch and plotting libraries are
only needed when their corresponding explicit save methods are used.

See the [recording guide](https://py.dreamlake.ai/native-experiments) and
[managed writer guide](https://py.dreamlake.ai/dash-writer) for the complete API.
The MIT attribution from the original ml-dash implementation is retained.

## Release process

Build with `uv build --project packages/dreamlake-dash`. The separately reviewed
`.github/workflows/publish-dash.yml` publishes the exact commit pushed to
`dash-publish-production`, after main's `tests` and `dash-package` checks pass.
The `dash-production-publish` environment restricts publishing to that branch.
Initial project creation uses its `PYPI_API_TOKEN` environment secret through
GitHub's publisher action; this is token authentication, not OIDC. Credentials
are absent from the build job and artifacts. Never publish from a local shell.

The workflow refuses an existing version and requires exactly the wheel and
sdist for the committed version. Uploads share a non-cancelling concurrency
lock. A partially published release must be reconciled using the retained exact
artifacts, never rebuilt under an existing filename. Publish this package before
an umbrella release that depends on its version. The existing `dreamlake`
release workflow remains independent and unchanged.
