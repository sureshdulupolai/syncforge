"""
SyncForge Python SDK — Client
==============================

The ``SyncForge`` class is the primary entry point for all SDK operations.
Create one instance per project (one API key) — typically in a module-level
``syncforge.py`` file, then import ``sf`` wherever needed::

    # syncforge.py (project root)
    import os
    from syncforge import SyncForge

    sf = SyncForge(api_key=os.environ['SYNCFORGE_API_KEY'])

    # views.py / routes.py / any handler
    from syncforge import sf
    sf.refresh('products')

Architecture Note
-----------------
``cache_query`` uses Django's cache framework for local data storage.
For multi-process deployments (Gunicorn / uWSGI), you **must** configure
a shared cache backend (Redis or Memcached). Without it, cache invalidation
triggered by ``sf.refresh()`` in one worker will not reach other workers.

The SDK detects single-process ``LocMemCache`` and emits a ``RuntimeWarning``
when called inside a multi-worker context, so you catch the issue early.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import threading
import time
import urllib.request
import urllib.error
import warnings
from typing import Any, Dict, List, Optional, Union

from .result import SyncResult
from .exceptions import (
    SyncForgeError,
    AuthError,
    TableNotFoundError,
    RateLimitError,
    NetworkError,
    ValidationError,
    ConfigurationError,
)

# ── Constants ──────────────────────────────────────────────────────────────────

#: Default production base URL — override via ``base_url`` for self-hosted.
DEFAULT_BASE_URL: str = "https://syncforge.dev/api"

#: Per-request HTTP timeout (seconds).
DEFAULT_TIMEOUT: int = 10

#: Maximum table name length accepted by the server.
_MAX_TABLE_NAME_LEN: int = 255

#: Maximum age (seconds) of an incoming request timestamp for replay protection.
_TIMESTAMP_TOLERANCE: int = 300  # 5 minutes

logger = logging.getLogger("syncforge")


# ── Stampede Protection — Per-key Locks ───────────────────────────────────────
# These locks are in-process only. They prevent multiple threads within the
# same worker from all hitting the database simultaneously on a cache miss
# (the "cache stampede" or "thundering herd" problem).
#
# For multi-process protection (Gunicorn workers), a shared Redis backend is
# required. The SDK warns when LocMemCache is detected.

_stampede_locks: Dict[str, threading.Lock] = {}
_stampede_locks_guard = threading.Lock()


def _get_stampede_lock(key: str) -> threading.Lock:
    """Return (creating if necessary) the per-key stampede-protection lock."""
    with _stampede_locks_guard:
        if key not in _stampede_locks:
            _stampede_locks[key] = threading.Lock()
        return _stampede_locks[key]


# ── LocMemCache Detection ──────────────────────────────────────────────────────

def _warn_if_locmem_cache() -> None:
    """
    Emit a ``RuntimeWarning`` if Django is using LocMemCache.

    LocMemCache is per-process and non-shared. In multi-worker deployments,
    each worker maintains its own independent cache store, meaning an
    invalidation signal from Worker A will not clear the cache in Workers B,
    C, or D. This leads to stale data being served after ``sf.refresh()``.
    """
    try:
        from django.core.cache import cache  # type: ignore[import]
        backend_fqn = f"{type(cache).__module__}.{type(cache).__name__}"
        if "locmem" in backend_fqn.lower():
            warnings.warn(
                "[SyncForge] LocMemCache detected as the Django cache backend. "
                "In multi-process deployments (Gunicorn, uWSGI, Uvicorn with "
                "multiple workers), cache invalidation will NOT propagate across "
                "workers. Set REDIS_URL in your environment and configure CACHES "
                "to use 'django.core.cache.backends.redis.RedisCache' for "
                "correct behaviour in production.",
                RuntimeWarning,
                stacklevel=4,
            )
    except ImportError:
        pass  # Not a Django project — no-op


# ── Internal Cache Engine ──────────────────────────────────────────────────────

class InMemoryCache:
    """
    Thread-safe, dict-backed fallback cache for non-Django frameworks (FastAPI/Flask).
    """
    def __init__(self) -> None:
        self._data: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key not in self._data:
                return default
            val, exp = self._data[key]
            if exp and time.time() > exp:
                del self._data[key]
                return default
            return val

    def set(self, key: str, value: Any, timeout: Optional[int] = None) -> None:
        with self._lock:
            exp = time.time() + timeout if timeout else 0
            self._data[key] = (value, exp)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def delete_many(self, keys: List[str]) -> None:
        with self._lock:
            for k in keys:
                self._data.pop(k, None)

_fallback_cache = InMemoryCache()

def _get_cache() -> Any:
    """
    Return Django's cache if available, else the internal fallback cache.
    """
    try:
        from django.core.cache import cache  # type: ignore[import]
        return cache
    except ImportError:
        return _fallback_cache


# ── Main Client ───────────────────────────────────────────────────────────────

class SyncForge:
    """
    Official Python client for the SyncForge data synchronisation platform.

    SyncForge is a **developer-controlled data synchronisation platform**.
    Rather than expiring data on a fixed TTL, it serves previously fetched
    data until the developer explicitly signals that the underlying data has
    changed::

        # Write to DB, then tell SyncForge data has changed.
        Product.objects.create(name="New Widget", price=9.99)
        sf.refresh("products")   # All clients now receive fresh data.

    Args:
        api_key:
            Your SyncForge API key (starts with ``sf_live_``).
        base_url:
            Override the API base URL. Useful for local development or
            self-hosted SyncForge instances.
        timeout:
            HTTP timeout in seconds for each request to the SyncForge API.
            Default: ``10``.
        silent:
            When ``True``, all ``SyncForgeError`` exceptions are caught,
            logged as warnings, and suppressed. Recommended in production so
            a SyncForge service interruption never propagates to your users.
            Default: ``False``.
        async_mode:
            When ``True``, every ``refresh()`` call runs in a background
            daemon thread and returns ``None`` immediately (fire-and-forget).
            Default: ``False``.
        sign_requests:
            When ``True``, outgoing requests include ``X-SF-Timestamp`` and
            ``X-SF-Signature`` headers for replay-attack protection.
            Default: ``True``.

    Raises:
        :class:`~syncforge.exceptions.ConfigurationError`:
            If ``api_key`` is empty or malformed.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        silent: bool = False,
        async_mode: bool = False,
        sign_requests: bool = True,
    ) -> None:
        if not api_key or not isinstance(api_key, str):
            raise ConfigurationError("api_key is required and must be a non-empty string.")
        api_key = api_key.strip()
        if not api_key.startswith("sf_"):
            raise ConfigurationError(
                f"api_key appears invalid (expected 'sf_live_...' prefix, got '{api_key[:8]}...'). "
                "Obtain a valid key from your SyncForge dashboard."
            )

        self._api_key      = api_key
        self._base_url     = base_url.rstrip("/")
        self._timeout      = timeout
        self._silent       = silent
        self._async        = async_mode
        self._sign_requests = sign_requests

    # ── Public API ─────────────────────────────────────────────────────────────

    def refresh(
        self,
        *tables: str,
    ) -> Union[SyncResult, List[SyncResult], None]:
        """
        Signal SyncForge that data in one or more tables has changed.

        Call this after any database write (INSERT, UPDATE, DELETE, or bulk
        operation) to invalidate cached data and notify connected clients.
        SyncForge does **not** poll for changes — the developer is responsible
        for calling ``refresh()`` when data changes.

        Args:
            *tables:
                One or more table names registered in your SyncForge dashboard.
                Table names are case-insensitive and normalised to lowercase.

        Returns:
            - A single :class:`~syncforge.result.SyncResult` when one table
              is given.
            - A ``list`` of :class:`~syncforge.result.SyncResult` objects when
              multiple tables are given.
            - ``None`` when ``async_mode=True`` (fire-and-forget).

        Raises:
            :class:`~syncforge.exceptions.ValidationError`:
                If no table names are provided, or a name is invalid.
            :class:`~syncforge.exceptions.AuthError`:
                If the API key is invalid or revoked.
            :class:`~syncforge.exceptions.TableNotFoundError`:
                If a table is not registered in the dashboard.
            :class:`~syncforge.exceptions.NetworkError`:
                If the SyncForge service is unreachable.
            :class:`~syncforge.exceptions.SyncForgeError`:
                For any other server-side error.

        Examples::

            # Single table
            sf.refresh('products')

            # Multiple tables in one call
            sf.refresh('products', 'categories', 'inventory')

            # Inspect the result
            result = sf.refresh('products')
            if result.ok:
                print(f"Sync successful. {result.calls_saved} DB reads saved.")
        """
        if not tables:
            raise ValidationError(
                "At least one table name is required.",
                field="tables",
            )
        for t in tables:
            self._validate_table_name(t)

        if self._async:
            thread = threading.Thread(
                target=self._refresh_all,
                args=(tables,),
                daemon=True,
            )
            thread.start()
            return None

        results = self._refresh_all(tables)
        return results[0] if len(results) == 1 else results

    def get_table(self, table_name: str) -> Any:
        """
        Fetch the automatically generated cache for a specific table.
        This retrieves data stored by cache_query() when no cache_key is explicitly provided.
        """
        self._validate_table_name(table_name)
        cache = _get_cache()
        return cache.get(f"sf_auto_{table_name}")

    def cache_query(
        self,
        table_name: str,
        cache_key: Any = None,
        queryset: Any = None,
        timeout: Optional[int] = 3600,
    ) -> List[Any]:
        """
        Fetch a queryset with intelligent cache-aside storage.

        On the first call the queryset is evaluated (hitting the database) and
        the result is stored in the configured cache backend. Subsequent calls
        with the same ``cache_key`` return the cached result without touching
        the database — until the cache is invalidated by ``sf.refresh()`` (via
        the ``@sync_model`` decorator) or the ``timeout`` elapses.

        **Stampede Protection**: When many threads simultaneously encounter a
        cache miss, only one thread evaluates the queryset. The rest wait for
        the first thread to populate the cache, then read from it. This
        prevents the "thundering herd" problem under high concurrency.

        **Multi-Process Warning**: In-process locking only protects threads
        within a single worker. For Gunicorn / uWSGI deployments, configure
        Redis as the cache backend so invalidation propagates across workers.

        Args:
            table_name:
                The database table name (matches the ``table_name`` in your
                ``@sync_model`` decorator or dashboard configuration).
            cache_key:
                A unique string identifying this cached dataset. Use
                per-user or per-client keys to avoid data leakage.
            queryset:
                A Django ``QuerySet`` (or any iterable) to evaluate on cache
                miss. Must be serialisable by the cache backend.
            timeout:
                Seconds before the cache entry expires automatically.
                ``None`` means no automatic expiration — the cache is
                invalidated only by an explicit ``sf.refresh()`` call.
                Default: ``3600`` (1 hour).

        Returns:
            A ``list`` of model instances (same objects ``list(queryset)``
            would return). All Django model attributes and methods are
            accessible on each item.

        Examples::

            # Basic usage
            products = sf.cache_query(
                table_name='core_product',
                cache_key='all_active_products',
                queryset=Product.objects.filter(active=True).order_by('name'),
                timeout=3600,
            )

            # Monthly cache — timeout=None, rotation via cache_key
            import datetime
            now = datetime.date.today()
            products = sf.cache_query(
                table_name='core_product',
                cache_key=f'products_{now.year}_{now.month}',
                queryset=Product.objects.filter(active=True),
                timeout=None,
            )
        """
        self._validate_table_name(table_name)
        
        # Handle backward compatibility / optional cache_key
        # If cache_key is not a string and queryset is None, the user passed the queryset as the 2nd positional argument.
        if queryset is None and cache_key is not None and not isinstance(cache_key, str):
            queryset = cache_key
            cache_key = None
            
        if queryset is None:
            raise ValueError("queryset must be provided.")
            
        if not cache_key:
            cache_key = f"sf_auto_{table_name}"
        elif not isinstance(cache_key, str):
            raise ValidationError("cache_key must be a non-empty string.", field="cache_key")

        cache = _get_cache()
        _warn_if_locmem_cache()

        # ── Fast path: cache hit ───────────────────────────────────────────────
        data = cache.get(cache_key)
        if data is not None:
            logger.debug("[SyncForge] cache_query cache HIT for key=%r", cache_key)
            # Report cache hit asynchronously — does not block the response.
            self._report_cache_hit_async(table_name)
            return data  # type: ignore[return-value]

        # ── Stampede protection: serialise DB access per cache key ────────────
        lock = _get_stampede_lock(cache_key)
        with lock:
            # Double-checked locking: another thread may have populated the
            # cache while we were waiting for the lock.
            data = cache.get(cache_key)
            if data is not None:
                logger.debug(
                    "[SyncForge] cache_query cache HIT (post-lock) for key=%r",
                    cache_key,
                )
                self._report_cache_hit_async(table_name)
                return data  # type: ignore[return-value]

            # ── Cache miss: evaluate queryset and populate cache ──────────────
            logger.debug("[SyncForge] cache_query cache MISS for key=%r — querying DB", cache_key)
            data = list(queryset)
            cache.set(cache_key, data, timeout)

            # Register this cache key in the table's invalidation registry so
            # that @sync_model can clear it when the table changes.
            self._register_cache_key(table_name, cache_key, timeout)

        return data  # type: ignore[return-value]

    def track_key(self, table_name: str, cache_key: str, timeout: Optional[int] = 3600) -> None:
        """
        Manually register a cache key to be cleared when sf.refresh(table_name) is called.
        Use this when manually setting cache with if/else instead of using sf.cache_query().
        """
        self._validate_table_name(table_name)
        if not cache_key or not isinstance(cache_key, str):
            raise ValidationError("cache_key must be a non-empty string.", field="cache_key")
        self._register_cache_key(table_name, cache_key, timeout)

    def ping(self) -> bool:
        """
        Check that your API key is valid and the SyncForge service is reachable.

        Returns:
            ``True`` if the health endpoint responds with a successful status.
            ``False`` if the request fails for any reason.

        Examples::

            if sf.ping():
                print("SyncForge is reachable.")
            else:
                print("SyncForge is unreachable or API key is invalid.")
        """
        try:
            url = f"{self._base_url}/v1/health/"
            self._request("GET", url)
            return True
        except Exception:
            return False

    def project_info(self) -> Dict[str, Any]:
        """
        Fetch project metadata and registered tables for this API key.

        Returns:
            A dict containing ``project``, ``slug``, ``tables``, and
            ``active_keys``.

        Raises:
            :class:`~syncforge.exceptions.AuthError`: Invalid API key.
            :class:`~syncforge.exceptions.NetworkError`: Service unreachable.
        """
        url = f"{self._base_url}/v1/project/"
        return self._request("GET", url)

    def list_tables(self) -> List[Dict[str, Any]]:
        """
        Return all tables registered in this project.

        Returns:
            List of dicts, each containing ``table_name``, ``sync_mode``,
            ``rows_count``, and ``database_calls_saved``.

        Raises:
            :class:`~syncforge.exceptions.AuthError`: Invalid API key.
            :class:`~syncforge.exceptions.NetworkError`: Service unreachable.
        """
        url  = f"{self._base_url}/v1/tables/"
        data = self._request("GET", url)
        return data.get("tables", [])

    def create_table(self, table_name: str, sync_mode: str = "event") -> bool:
        """
        Register a new table in this project programmatically.

        This is called automatically by the ``@sync_model`` decorator.
        You rarely need to call it directly.

        Args:
            table_name:
                The database table name (e.g. ``'core_product'``).
            sync_mode:
                One of ``'event'``, ``'manual'``, ``'schedule_5m'``,
                ``'schedule_1h'``, ``'schedule_1d'``, ``'schedule_30d'``,
                ``'hybrid'``.

        Returns:
            ``True`` if the table was newly created.
            ``False`` if it already existed.

        Raises:
            :class:`~syncforge.exceptions.ValidationError`:
                If ``table_name`` is empty or contains invalid characters.
            :class:`~syncforge.exceptions.AuthError`: Invalid API key.
        """
        self._validate_table_name(table_name)
        table_name = table_name.strip().lower()
        url = f"{self._base_url}/v1/tables/"
        try:
            res = self._request("POST", url, json_data={"table_name": table_name, "sync_mode": sync_mode})
            return bool(res.get("created", False))
        except SyncForgeError as exc:
            if self._silent:
                warnings.warn(
                    f"[SyncForge] create_table failed for '{table_name}': {exc}",
                    stacklevel=2,
                )
                return False
            raise

    def delete_table(self, table_name: str) -> bool:
        """
        Remove a table from this project's SyncForge configuration.

        Args:
            table_name: The table to remove (case-insensitive).

        Returns:
            ``True`` if deleted, ``False`` if the table was not found.

        Raises:
            :class:`~syncforge.exceptions.ValidationError`:
                If ``table_name`` is empty.
            :class:`~syncforge.exceptions.AuthError`: Invalid API key.
        """
        self._validate_table_name(table_name)
        table_name = table_name.strip().lower()

        import urllib.parse
        url = f"{self._base_url}/v1/tables/?table_name={urllib.parse.quote(table_name)}"
        try:
            res = self._request("DELETE", url)
            return bool(res.get("deleted", False))
        except SyncForgeError as exc:
            if self._silent:
                warnings.warn(
                    f"[SyncForge] delete_table failed for '{table_name}': {exc}",
                    stacklevel=2,
                )
                return False
            raise

    # ── Internal Helpers ───────────────────────────────────────────────────────

    def _validate_table_name(self, table_name: str) -> None:
        """Validate a table name, raising ValidationError if it's unacceptable."""
        if not table_name or not isinstance(table_name, str):
            raise ValidationError("table_name must be a non-empty string.", field="table_name")
        if len(table_name.strip()) > _MAX_TABLE_NAME_LEN:
            raise ValidationError(
                f"table_name cannot exceed {_MAX_TABLE_NAME_LEN} characters.",
                field="table_name",
            )
        stripped = table_name.strip()
        # Only allow alphanumeric characters and underscores (standard DB table names).
        import re
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', stripped):
            raise ValidationError(
                f"table_name '{stripped}' contains invalid characters. "
                "Only letters, digits, and underscores are allowed, "
                "and the name must not start with a digit.",
                field="table_name",
            )

    def _register_cache_key(
        self,
        table_name: str,
        cache_key: str,
        timeout: Optional[int],
    ) -> None:
        """
        Add ``cache_key`` to the table's invalidation registry in the cache.

        The ``@sync_model`` signal handler reads this registry to know which
        cache keys to delete when the table changes.
        """
        try:
            cache = _get_cache()
            registry_key = f"sf_registry_{table_name}"
            # Registry TTL: at least as long as the data TTL, or 24 hours if no timeout.
            registry_ttl = timeout if timeout is not None else 86400
            existing_keys: set = cache.get(registry_key) or set()
            existing_keys.add(cache_key)
            cache.set(registry_key, existing_keys, registry_ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SyncForge] Failed to register cache key '%s': %s", cache_key, exc)

    def _report_cache_hit_async(self, table_name: str) -> None:
        """
        Fire-and-forget: tell the SyncForge server that a cache hit occurred
        for ``table_name``. This increments ``database_calls_saved`` with the
        correct semantics (cache hit = DB call avoided).

        Runs in a background daemon thread so it never delays the response.
        """
        def _report() -> None:
            try:
                url = f"{self._base_url}/v1/cache-hit/{table_name}/"
                self._request("POST", url)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[SyncForge] cache hit report failed (non-critical): %s", exc)

        thread = threading.Thread(target=_report, daemon=True)
        thread.start()

    def _refresh_all(self, tables: tuple) -> List[SyncResult]:
        """Execute ``_sync_one`` for each table, handling errors per ``silent``."""
        results: List[SyncResult] = []
        for table in tables:
            # 1. Invalidate local cache FIRST (framework-agnostic)
            self._invalidate_local_cache(table)
            
            # 2. Notify SyncForge server
            try:
                results.append(self._sync_one(table))
            except SyncForgeError as exc:
                if self._silent:
                    warnings.warn(
                        f"[SyncForge] {exc} (table={table!r})",
                        stacklevel=4,
                    )
                    results.append(SyncResult(
                        ok=False,
                        table=table,
                        message=str(exc),
                        status_code=getattr(exc, "status_code", None) or 0,
                    ))
                else:
                    raise
        return results

    def _sync_one(self, table: str) -> SyncResult:
        """Send a refresh signal for a single table to the SyncForge server."""
        table = table.strip().lower()
        url   = f"{self._base_url}/v1/sync/{table}/"
        data  = self._request("POST", url)

        return SyncResult(
            ok=data.get("status") == "ok",
            table=data.get("table", table),
            project=data.get("project"),
            sync_mode=data.get("sync_mode"),
            calls_saved=data.get("database_calls_saved", 0),
            message=data.get("message", ""),
            raw=data,
            status_code=200,
        )

    def _invalidate_local_cache(self, table_name: str) -> None:
        """
        Delete all cache entries registered under ``table_name``'s invalidation
        registry. Works across all frameworks.
        """
        try:
            cache = _get_cache()
            registry_key = f"sf_registry_{table_name}"
            keys: set = cache.get(registry_key) or set()
            if keys:
                # Provide a generic delete_many approach if the backend supports it
                if hasattr(cache, 'delete_many'):
                    cache.delete_many(list(keys))
                else:
                    for k in keys:
                        cache.delete(k)
                cache.delete(registry_key)
                logger.debug(
                    "[SyncForge] Invalidated %d cache key(s) for table '%s'.",
                    len(keys), table_name,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SyncForge] Cache invalidation failed for table '%s': %s",
                table_name, exc,
            )

    # ── HTTP Layer ─────────────────────────────────────────────────────────────

    def _sign_request(
        self,
        method: str,
        url: str,
        body: bytes,
        timestamp: str,
    ) -> str:
        """
        Compute an HMAC-SHA256 request signature.

        Signing string format::

            METHOD\\nURL_PATH\\nTIMESTAMP\\nSHA256(BODY)

        The signature allows the server to verify:
        1. The request was made by a holder of the API key.
        2. The request was made recently (within ±5 minutes), preventing replay
           attacks where an eavesdropper replays a previously captured request.

        Returns:
            Hex-encoded HMAC-SHA256 signature string.
        """
        import urllib.parse
        path       = urllib.parse.urlparse(url).path
        body_hash  = hashlib.sha256(body).hexdigest()
        signing_str = f"{method}\n{path}\n{timestamp}\n{body_hash}"
        sig = _hmac.new(
            self._api_key.encode("utf-8"),
            signing_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return sig

    def _request(
        self,
        method: str,
        url: str,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute an authenticated HTTP request to the SyncForge API.

        Authentication is via the ``X-API-Key`` header.
        When ``sign_requests=True``, also sends ``X-SF-Timestamp`` and
        ``X-SF-Signature`` for replay-attack protection.

        Args:
            method:    HTTP method (``'GET'``, ``'POST'``, ``'DELETE'``).
            url:       Full request URL.
            json_data: Optional dict to serialise as the request body.

        Returns:
            Parsed JSON response as a dict.

        Raises:
            :class:`~syncforge.exceptions.AuthError`: HTTP 401/403.
            :class:`~syncforge.exceptions.TableNotFoundError`: HTTP 404.
            :class:`~syncforge.exceptions.RateLimitError`: HTTP 429.
            :class:`~syncforge.exceptions.NetworkError`: Connection / timeout.
            :class:`~syncforge.exceptions.SyncForgeError`: Other server errors.
        """
        # Build request body.
        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")
        elif method in ("POST", "PUT", "PATCH"):
            body = b"{}"
        else:
            body = b""

        timestamp = str(int(time.time()))

        headers: Dict[str, str] = {
            "X-API-Key":    self._api_key,
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   "syncforge-python/1.1.0",
        }

        if self._sign_requests:
            headers["X-SF-Timestamp"] = timestamp
            headers["X-SF-Signature"] = self._sign_request(method, url, body, timestamp)

        req = urllib.request.Request(url, data=body or None, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}

        except urllib.error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            payload: Dict[str, Any] = {}
            try:
                payload = json.loads(raw_body)
            except Exception:  # noqa: BLE001
                pass

            error_msg = payload.get("error", raw_body or str(exc.reason))
            code = exc.code

            if code in (401, 403):
                raise AuthError(f"Authentication failed: {error_msg}", status_code=code)
            if code == 404:
                raise TableNotFoundError(
                    f"Table not found — register it in your SyncForge dashboard. {error_msg}",
                    status_code=code,
                )
            if code == 429:
                raise RateLimitError(
                    f"Rate limit exceeded: {error_msg}",
                    status_code=code,
                )
            raise SyncForgeError(f"Server error {code}: {error_msg}", status_code=code)

        except urllib.error.URLError as exc:
            raise NetworkError(
                f"Could not connect to SyncForge ({url}): {exc.reason}"
            ) from exc

        except TimeoutError:
            raise NetworkError(
                f"Request to SyncForge timed out after {self._timeout}s. "
                "Check your network and consider increasing the timeout parameter."
            )
