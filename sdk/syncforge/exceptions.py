class SyncForgeError(Exception):
    """Base exception for all SyncForge SDK errors."""
    def __init__(self, message: str, status_code: int = None):
        super().__init__(message)
        self.status_code = status_code


class AuthError(SyncForgeError):
    """Raised when the API key is missing, invalid, or revoked."""


class TableNotFoundError(SyncForgeError):
    """Raised when the table is not registered in the SyncForge dashboard."""


class RateLimitError(SyncForgeError):
    """Raised when the API rate limit is exceeded."""


class NetworkError(SyncForgeError):
    """Raised when the HTTP request fails (timeout, DNS, etc.)."""
