"""Device authorization flow (RFC 8628) client for dreamlake."""

import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .constants import VUER_AUTH_URL, CLIENT_ID, DEFAULT_SCOPE
from .device_secret import hash_device_secret
from .exceptions import (
    DeviceCodeExpiredError,
    AuthorizationDeniedError,
    TokenExchangeError,
)


@dataclass
class DeviceFlowResponse:
    """Response from device flow initiation."""

    user_code: str
    device_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceFlowClient:
    """Client for OAuth 2.0 Device Authorization Flow (RFC 8628)."""

    def __init__(self, device_secret: str, dreamlake_server_url: str):
        """Initialize device flow client.

        Args:
            device_secret: Persistent device secret for this client.
            dreamlake_server_url: DreamLake server URL for token exchange.
        """
        self.device_secret = device_secret
        self.dreamlake_server_url = dreamlake_server_url.rstrip("/")

    def start_device_flow(self, scope: str = DEFAULT_SCOPE) -> DeviceFlowResponse:
        """Initiate device authorization flow with vuer-auth.

        Args:
            scope: OAuth scopes to request.

        Returns:
            DeviceFlowResponse with user code and verification URI.

        Raises:
            httpx.HTTPError: If request fails.
        """
        response = httpx.post(
            f"{VUER_AUTH_URL}/api/device/start",
            json={
                "client_id": CLIENT_ID,
                "scope": scope,
                "device_secret_hash": hash_device_secret(self.device_secret),
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()

        return DeviceFlowResponse(
            user_code=data["user_code"],
            device_code=data.get("device_code", ""),
            verification_uri=data["verification_uri"],
            verification_uri_complete=data.get(
                "verification_uri_complete",
                f"{data['verification_uri']}?code={data['user_code'].replace('-', '')}",
            ),
            expires_in=data.get("expires_in", 600),
            interval=data.get("interval", 5),
        )

    def poll_for_token(
        self,
        max_attempts: int = 120,
        progress_callback: Optional[callable] = None,
    ) -> str:
        """Poll vuer-auth for authorization completion.

        Args:
            max_attempts: Maximum polling attempts (default: 120 = 10 min at 5s intervals).
            progress_callback: Optional callback(elapsed_seconds) for progress updates.

        Returns:
            Vuer-auth access token (JWT).

        Raises:
            DeviceCodeExpiredError: If device code expires.
            AuthorizationDeniedError: If user denies authorization.
            TimeoutError: If polling times out.
        """
        device_secret_hash = hash_device_secret(self.device_secret)

        for attempt in range(max_attempts):
            elapsed = attempt * 5

            if progress_callback:
                progress_callback(elapsed)

            try:
                response = httpx.post(
                    f"{VUER_AUTH_URL}/api/device/poll",
                    json={
                        "client_id": CLIENT_ID,
                        "device_secret_hash": device_secret_hash,
                    },
                    timeout=10.0,
                )

                if response.status_code == 200:
                    return response.json()["access_token"]

                error = response.json().get("error")

                if error == "authorization_pending":
                    time.sleep(5)
                    continue
                elif error == "expired_token":
                    raise DeviceCodeExpiredError(
                        "Device code expired. Please run 'dreamlake login' again."
                    )
                elif error == "access_denied":
                    raise AuthorizationDeniedError("User denied authorization request.")
                elif error == "slow_down":
                    time.sleep(10)
                    continue
                else:
                    raise TokenExchangeError(f"Device flow error: {error}")

            except httpx.HTTPError:
                time.sleep(5)
                continue

        raise TimeoutError(
            "Authorization timed out after 10 minutes. Please run 'dreamlake login' again."
        )

    def exchange_token(self, vuer_auth_token: str) -> str:
        """Exchange vuer-auth token for a permanent dreamlake token.

        Args:
            vuer_auth_token: Temporary vuer-auth JWT access token.

        Returns:
            Permanent dreamlake token string.

        Raises:
            TokenExchangeError: If exchange fails.
        """
        try:
            response = httpx.post(
                f"{self.dreamlake_server_url}/auth/exchange",
                headers={"Authorization": f"Bearer {vuer_auth_token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

            token = data.get("dreamlake_token")
            if not token:
                raise TokenExchangeError("Server response missing dreamlake_token field")

            return token

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise TokenExchangeError(
                    "Vuer-auth token invalid or expired. Please try logging in again."
                )
            raise TokenExchangeError(
                f"Token exchange failed: {e.response.status_code} {e.response.text}"
            )
        except httpx.HTTPError as e:
            raise TokenExchangeError(f"Network error during token exchange: {e}")
