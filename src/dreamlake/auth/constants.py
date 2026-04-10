"""Authentication constants for dreamlake."""

import os

# Vuer-auth server URL
VUER_AUTH_URL = os.environ.get("VUER_AUTH_URL", "https://auth.vuer.ai")

# OAuth client ID for dreamlake
CLIENT_ID = "dreamlake-client"

# Default OAuth scopes
DEFAULT_SCOPE = "openid profile email"
