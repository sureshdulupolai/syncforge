"""
SyncForge SDK — Exception Hierarchy
====================================

All SyncForge errors inherit from ``SyncForgeError`` so callers can catch
the entire family with a single ``except SyncForgeError`` clause, or catch
individual subtypes for fine-grained handling.

Example::

    from syncforge.exceptions import SyncForgeError, AuthError, NetworkError

    try:
        sf.refresh('products')
    except AuthError:
        print("API key invalid — check your dashboard.")
    except NetworkError:
        print("SyncForge unreachable — will retry later.")
    except SyncForgeError as exc:
        print(f"Unexpected SyncForge error: {exc}")
"""
from __future__ import annotations


class SyncForgeError(Exception):
    """
    Base exception for all SyncForge SDK errors.

    All other SyncForge exceptions inherit from this class.

    Attributes:
        status_code: HTTP status code from the server response, if applicable.
    """
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({str(self)!r}, status_code={self.status_code})"


class AuthError(SyncForgeError):
    """
    Raised when the API key is missing, invalid, expired, or revoked.

    Resolution: Generate a new key in your SyncForge dashboard.
    """


class TableNotFoundError(SyncForgeError):
    """
    Raised when a table name is not registered in the SyncForge dashboard.

    Resolution: Register the table via ``sf.create_table()`` or in the dashboard,
    or use the ``@sync_model`` decorator which registers automatically.
    """


class RateLimitError(SyncForgeError):
    """
    Raised when the project's API rate limit is exceeded.

    The ``status_code`` will be 429. Respect the ``Retry-After`` value
    if present in the response, or implement exponential backoff.
    """


class NetworkError(SyncForgeError):
    """
    Raised when the HTTP request to SyncForge fails due to a network-level
    problem: DNS resolution failure, connection refused, or request timeout.

    The SyncForge service may be temporarily unreachable.
    Use ``silent=True`` on the ``SyncForge`` client in production to log
    this as a warning instead of raising.
    """


class ValidationError(SyncForgeError):
    """
    Raised when input to an SDK method fails validation before a network
    request is made.

    Examples: empty table name, invalid characters in key, negative timeout.
    """
    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message, status_code=None)
        self.field: str | None = field


class ConfigurationError(SyncForgeError):
    """
    Raised when the SyncForge client is misconfigured in a way that will
    prevent correct operation.

    Examples:
    - ``api_key`` not provided.
    - ``base_url`` has an invalid scheme.
    - Cache backend is incompatible with the deployment environment.
    """


class CacheError(SyncForgeError):
    """
    Raised when a cache backend operation fails and the failure cannot be
    silently recovered (e.g., serialisation error, corrupted cache entry).

    Note: Transient cache unavailability (e.g., Redis restart) does NOT raise
    this error — the SDK falls back to querying the database directly.
    """

class SyncForgeWAFError(SyncForgeError):
    """
    Raised when the built-in WAF (Rate Limiter) blocks a request due to
    excessive cache queries from a single IP address.

    Attributes:
        block_time: The number of seconds the IP is blocked for.
    """
    def __init__(self, message: str, block_time: int) -> None:
        super().__init__(message, status_code=429)
        self.block_time = block_time

