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

SDK namespace precedence is explicit argument/path, then the selected default,
then the personal namespace. A stored default is bound to both server URL and
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

Saved authentication and defaults cannot be changed while `DREAMLAKE_API_KEY`,
`DREAMLAKE_REMOTE`, or `DREAMLAKE_IDENTITY=machine` overrides are active. Machine
identity also ignores saved user namespace defaults. Explicit namespaces remain
available for per-call selection. These settings do not modify Git credentials.
