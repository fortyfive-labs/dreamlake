"""DreamLake authentication module."""

from .constants import VUER_AUTH_URL, CLIENT_ID, DEFAULT_SCOPE
from .exceptions import (
    AuthenticationError,
    NotAuthenticatedError,
    DeviceCodeExpiredError,
    AuthorizationDeniedError,
    TokenExchangeError,
    StorageError,
)
from .device_secret import generate_device_secret, hash_device_secret, get_or_create_device_secret
from .device_flow import DeviceFlowClient, DeviceFlowResponse
from .token_storage import get_token_storage, decode_jwt_payload

__all__ = [
    "VUER_AUTH_URL",
    "CLIENT_ID",
    "DEFAULT_SCOPE",
    "AuthenticationError",
    "NotAuthenticatedError",
    "DeviceCodeExpiredError",
    "AuthorizationDeniedError",
    "TokenExchangeError",
    "StorageError",
    "generate_device_secret",
    "hash_device_secret",
    "get_or_create_device_secret",
    "DeviceFlowClient",
    "DeviceFlowResponse",
    "get_token_storage",
    "decode_jwt_payload",
]
