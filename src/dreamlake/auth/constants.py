"""Authentication constants for dreamlake."""

import os

# Vuer-auth server URL
VUER_AUTH_URL = os.environ.get("VUER_AUTH_URL", "https://auth.vuer.ai")

# OAuth client ID for dreamlake
# Same OAuth client as the web app — one application, one access policy.
CLIENT_ID = "dreamlake-app"

# Default OAuth scopes
DEFAULT_SCOPE = "openid profile email"
