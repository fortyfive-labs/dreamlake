"""Authentication exceptions for dreamlake."""


class AuthenticationError(Exception):
    """Base authentication error."""
    pass


class NotAuthenticatedError(AuthenticationError):
    """Raised when no token is available."""
    pass


class DeviceCodeExpiredError(AuthenticationError):
    """Raised when the device code expires before authorization."""
    pass


class AuthorizationDeniedError(AuthenticationError):
    """Raised when the user denies the authorization request."""
    pass


class TokenExchangeError(AuthenticationError):
    """Raised when token exchange with dreamlake server fails."""
    pass


class StorageError(AuthenticationError):
    """Raised when token storage backend fails."""
    pass
