# Authentication profiles and default namespaces

A profile selects your account and server. A namespace selects the workspace
used by resource paths without an explicit namespace. You can use `fortyfive`
and `dyna-robotics` with one account if that account belongs to both
organizations. Selecting a namespace never grants membership or switches identity.

Python 0.57.0 shares profiles with the standalone DreamLake CLI:

```python
from dreamlake.auth import Auth

auth = Auth()
auth.profiles()                 # Safe metadata; no tokens
auth.status(verify=True)        # Verify identity with the server
auth.switch("work")             # Select a previously saved profile
auth.namespaces()               # Personal namespace + organization memberships
auth.set_default_namespace("fortyfive")
auth.set_default_namespace("dyna-robotics")
auth.clear_default_namespace()  # Return to personal namespace fallback
```

`Auth.login(token, profile="work", server="https://api.dreamlake.ai")` verifies
the token before saving it. Pass `activate=False` to save an inactive profile.
`environment="prod"` or `environment="staging"` selects the built-in deployment
and its authentication provider. An explicit `server=` takes precedence; custom
environment labels require `server=`. The default profile name is `prod`. `Auth.refresh(token=...)`
replaces the current profile's credentials. To reauthorize through the existing
device flow, supply `on_device_code=callback`; the callback receives a
`DeviceFlowResponse` containing the verification URL and user code. For example:

```python
import webbrowser

auth.refresh(on_device_code=lambda code: webbrowser.open(code.verification_uri_complete))
```

There is no refresh-token grant or arbitrary scope escalation. `auth.token()`
explicitly returns the active token; avoid logging it. `auth.logout()` removes
local saved credentials, without revoking tokens on the server. Removing the
active profile selects the next saved profile; removing the last one clears the
local connection while preserving unrelated settings such as the device secret. Pass `profile=`
to `token`, `refresh`, `logout`, `namespaces`, `set_default_namespace`, or
`clear_default_namespace` to address a particular profile. `switch` requires a
saved profile name. `status(verify=False)` performs no server requests and returns `verified=False`;
`authenticated=True` then means a credential is present. Use `verify=True` to
verify it with the server. Profiles are active only when their server and token
match the effective connection; environment overrides are reported explicitly.

Defaults are verified against `/auth/me` and the server's organization
membership list before anything is saved. Failed checks leave the existing
default intact. Each profile retains its default when you switch away and back.
Refreshing the same personal identity on the same server preserves its default;
logging in as another personal identity clears it. Server authorization still
applies to every operation, including after membership changes.

SDK namespace precedence is explicit argument/path, then the selected repository
`.dreamrc` namespace, then the credential-bound global default, then the personal
namespace. A stored default is bound to both server URL and
token fingerprint, so overriding credentials or server cannot inherit another
account's default. `DreamLakeClient.resolve_namespace(namespace=None)` and
`RemoteClient.resolve_namespace()` use this policy. For identity inspection,
`get_auth_me()` / `get_me()` always return the authenticated account;
`RemoteClient.resolve_personal_namespace()` and
`dreamlake._session.get_personal_namespace()` ignore workspace defaults.

Profiles use `~/.dreamlake/auth.yml`; the active profile is projected into
`tokens.json` and `config.json`. Credential files use owner-only permissions.
The default projection contains the namespace, server, and token SHA-256, never
an extra copy of the token. `Auth(config_dir=...)` supports an isolated store;
`transport=` accepts a caller-owned synchronous httpx transport. The SDK reads
the current CLI token projection before falling back to legacy token storage.

Saved global authentication and defaults cannot be changed while `DREAMLAKE_API_KEY`,
`DREAMLAKE_REMOTE`, or `DREAMLAKE_IDENTITY=machine` overrides are active. Machine
identity also ignores saved user namespace defaults. Explicit namespaces remain
available for per-call selection. These settings do not modify Git credentials.


## Repository namespaces (Python 0.58.0+)

A repository can select its workspace directly in `.dreamrc`:

```yaml
# Robotics project
namespace: dyna-robotics
```

Only the top-level namespace is used for this selection. Credentials, server
URLs, and authentication profiles always come from the global login store or
explicit client/environment settings. A repository file cannot change them.

```python
from dreamlake.auth import Auth

auth = Auth(cwd="/path/to/repository")
auth.set_default_namespace("fortyfive", scope="global")
auth.set_default_namespace("dyna-robotics", scope="local")
assert auth.get_namespace() == "dyna-robotics"
assert auth.get_namespace(scope="global") == "fortyfive"
status = auth.status(verify=True)
# status["namespaceSource"] == "local"
# status["configFile"] is the actual .dreamrc path
# status["configScope"] == "local"
# status["profileDefaultNamespace"] == "fortyfive" (None without an explicit global default)
auth.clear_default_namespace(scope="local")  # Keeps the file; inherits global.
```

`scope="auto"` is the default. Discovery starts at `cwd` (the process working
directory by default) and walks upward to the nearest `.dreamrc`, checking the
Git root itself and stopping there. It stops before the home directory, which
is not a repository configuration source. A `.git` directory or worktree `.git`
file is a boundary. A nearer file takes precedence over any ancestor file.

With an existing file, auto reads its namespace and auto writes target that same
file. A file without a namespace inherits the global default when read, while
auto writes still target the file. Without a discovered file, auto reads and
writes use the global profile. `scope="global"` bypasses repository discovery
and parsing entirely, including an invalid repository file.

`scope="local"` reads require a discovered file. Local writes without one create
`.dreamrc` at the nearest Git root, or in `cwd` outside Git. Creating one at the
excluded home boundary is rejected. Reset removes only the namespace field and
preserves the file, so future auto reads inherit the global or personal default.
Invalid YAML, a non-mapping document, duplicate keys, and invalid namespace values
raise errors; they never silently fall back to a global namespace. The namespace
key and value must be literal: tags, anchors, or aliases on them are rejected.
A namespace present only through a YAML merge is not a local selection.

The parser keeps custom tags such as `!providers` inert. Editing preserves other
YAML values, comments, quotes, and anchors. YAML formatting may be normalized by
the round-trip writer. Setting a namespace verifies membership first; rejection
leaves the file unchanged and does not create one. Reading an imported valid file
uses its namespace directly, without a separate approval or override store.
Local writes can use environment credentials for membership verification while
leaving the global credential store untouched.

`Auth(scope="local", cwd=...)` sets defaults for namespace operations. The
`status`, `namespaces`, `get_namespace`, `set_default_namespace`, and
`clear_default_namespace` methods accept per-call `scope` and `cwd` overrides.
`namespaces()` marks the effective namespace with `selected=True`. Login, token,
profile switching, refresh, and logout always operate on the global credential
store, independent of repository namespace scope. For SDK clients,
`resolve_namespace(namespace=None, *, scope="auto", cwd=None)` applies the same
precedence; `dreamlake._session.get_namespace` also accepts these scope options.
Explicit namespace arguments bypass repository configuration entirely.
