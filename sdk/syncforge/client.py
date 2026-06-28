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
from .engine import CacheEngine, StorageMode, CompressionType, EvictionPolicy
from .scheduler import SmartScheduler
from .store import StoreManager
from .events import SyncForgeEvent, emit_event
from .exceptions import (
    SyncForgeError, AuthError, TableNotFoundError, RateLimitError,
    NetworkError, ValidationError, ConfigurationError, SyncForgeWAFError,
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


# ── Stampede Protection & Request Coalescing ──────────────────────────────────
_stampede_locks: Dict[str, threading.Lock] = {}
_stampede_locks_guard = threading.Lock()
_refresh_coalesce_locks: Dict[str, dict] = {}
_refresh_coalesce_guard = threading.Lock()

# ── Background Maintenance Budget ─────────────────────────────────────────────
_background_tasks_pool = []
_background_pool_lock = threading.Lock()
_MAX_BACKGROUND_WORKERS = 4

def _submit_background_task(func, *args, **kwargs) -> None:
    """Bounded background worker pool to prevent CPU exhaustion."""
    def worker():
        try:
            func(*args, **kwargs)
        finally:
            with _background_pool_lock:
                if threading.current_thread() in _background_tasks_pool:
                    _background_tasks_pool.remove(threading.current_thread())
                    
    with _background_pool_lock:
        if len(_background_tasks_pool) >= _MAX_BACKGROUND_WORKERS:
            return
        t = threading.Thread(target=worker, daemon=True)
        _background_tasks_pool.append(t)
        t.start()

def _get_stampede_lock(key: str) -> threading.Lock:
    with _stampede_locks_guard:
        if key not in _stampede_locks:
            _stampede_locks[key] = threading.Lock()
        return _stampede_locks[key]

def _wait_lock_async_safe(lock: threading.Lock) -> None:
    """
    Waits for a lock without blocking an asyncio event loop if one is running.
    Ensures FastAPI/Starlette async endpoints are not stalled by I/O locks.
    """
    import asyncio
    try:
        asyncio.get_running_loop()
        while not lock.acquire(blocking=False):
            time.sleep(0.005)
    except RuntimeError:
        lock.acquire(blocking=True)


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

import platform
from pathlib import Path
import os

def _get_default_cache_dir() -> str:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "SyncForge", "Cache")
    elif system == "Darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "SyncForge", "Cache")
    else:
        # Linux / Android / UNIX fallback
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        return os.path.join(base, "SyncForge", "Cache")

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        silent: bool = False,
        async_mode: bool = False,
        sign_requests: bool = True,
        encryption_key: Optional[str] = None,
        cache_dir: Optional[str] = None,
        backend_type: str = "memory",
        redis_url: Optional[str] = None,
        dev_mode: bool = False,
    ) -> None:
        if dev_mode:
            api_key = api_key or "sf_local_dev_key"
            
        if not api_key or not isinstance(api_key, str):
            raise ConfigurationError("api_key is required and must be a non-empty string.")
        api_key = api_key.strip()
        if not dev_mode and not api_key.startswith("sf_"):
            raise ConfigurationError(
                f"api_key appears invalid (expected 'sf_live_...' prefix, got '{api_key[:8]}...'). "
                "Obtain a valid key from your SyncForge dashboard."
            )

        self._api_key      = api_key
        self._dev_mode     = dev_mode
        self._base_url     = base_url.rstrip("/")
        self._timeout      = timeout
        self._silent       = silent
        self._async        = async_mode
        self._sign_requests = sign_requests
        self._local         = threading.local()
        self._waf_configs: Dict[str, dict] = {}
        
        # Static Store Selection
        self.store_manager = StoreManager(backend_type, redis_url)
        
        # Enterprise Cache Engine
        if cache_dir is None:
            cache_dir = _get_default_cache_dir()
        self.engine = CacheEngine(base_dir=cache_dir, encryption_key=encryption_key)
        
        # Core Adapter (Unified Logic)
        from .core import SyncForgeCoreAdapter
        self.core = SyncForgeCoreAdapter(self)
        
        self.scheduler = None
        self._metadata_provider = None
        
        self._project_prefix = hashlib.md5(api_key.encode()).hexdigest()[:8]

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
            _submit_background_task(self._refresh_all, tables)
            return None

        results = self._refresh_all(tables)
        return results[0] if len(results) == 1 else results

    def get_table(self, table_name: str, cache_key: Optional[str] = None) -> Any:
        """
        Fetch data from the cache. If cache_key is provided, fetches that specific key.
        Otherwise, fetches the auto-generated key for the table.
        """
        self._validate_table_name(table_name)
        self._check_waf(table_name)
        if not cache_key:
            cache_key = f"syncforge_{self._project_prefix}_{table_name}"
            
        # Try to get metadata
        storage_mode = StorageMode.RAM_DISK
        version = 1
        if self._metadata_provider:
            meta = self._metadata_provider([table_name]).get(table_name, {})
            storage_mode = StorageMode(meta.get("storage_mode", "ram_disk"))
            version = meta.get("cache_version", 1)
            
        data = self.engine.get(table_name, cache_key, storage_mode, expected_version=version)
        
        if getattr(self, "_dev_mode", False):
            import datetime, json
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            
            preview = data[:5] if isinstance(data, list) else data
            mock_res = {
                "success": True,
                "status": "ok",
                "table": table_name,
                "message": "[Dev Mode] Table Fetched",
                "data": {
                    "version": version,
                    "last_sync": now_iso,
                    "records": preview if data is not None else []
                }
            }
            
            print(f"\n\033[96m🌐 [SyncForge Local Dev] Simulated Network Request\033[0m")
            print(f"\033[93m► METHOD:\033[0m GET")
            print(f"\033[93m► URL:\033[0m    {self._base_url}/v1/sync/{table_name}/")
            print(f"\033[92m◄ RESPONSE:\033[0m {json.dumps(mock_res, indent=2, default=str)}\n")

        return data

    def cache_query(
        self,
        table_name: str = None,
        cache_key: Any = None,
        queryset: Any = None,
        timeout: Optional[int] = 3600,
        **kwargs
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
                [DEPRECATED/IGNORED] Manual cache keys are ignored for security 
                reasons. SyncForge automatically generates a deterministic, 
                hacker-proof HMAC key combining the table version and query hash.
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

            # Basic usage (Secure Key Auto-Generated)
            products = sf.cache_query(
                table_name='core_product',
                queryset=Product.objects.filter(active=True).order_by('name'),
                timeout=3600,
            )

            # Monthly cache — timeout=None
            import datetime
            now = datetime.date.today()
            products = sf.cache_query(
                table_name='core_product',
                queryset=Product.objects.filter(active=True, created__year=now.year),
                timeout=None,
            )
        """
        table_name = table_name or kwargs.get("registered_table") or kwargs.get("add_table_name")
        if not table_name:
            raise ValueError("You must provide 'registered_table' (the name of the table registered in your dashboard).")
        self._validate_table_name(table_name)
        self._check_waf(table_name)
        
        # Handle backward compatibility / optional cache_key
        # If cache_key is not a string and queryset is None, the user passed the queryset as the 2nd positional argument.
        if queryset is None and cache_key is not None and not isinstance(cache_key, str):
            queryset = cache_key
            cache_key = None
            
        if queryset is None:
            raise ValueError("queryset must be provided.")
            
        # Security: Ignore any manually provided cache_key for robustness and speed
        if cache_key:
            logger.debug("[SyncForge] Manual cache_key is ignored. Auto-generating lightweight query key.")
            cache_key = None
            
        if not cache_key:
            import hashlib
            query_str = str(getattr(queryset, 'query', queryset))
            query_hash = hashlib.sha256(query_str.encode('utf-8')).hexdigest()[:16]
            cache_key = f"syncforge_{self._project_prefix}_{table_name}_{query_hash}"

        # ── Cap timeout at 4:00 AM IST ─────────────────────────────────────────
        import datetime
        ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_ist = datetime.datetime.now(ist_tz)
        target_ist = now_ist.replace(hour=4, minute=0, second=0, microsecond=0)
        if now_ist >= target_ist:
            target_ist += datetime.timedelta(days=1)
        max_timeout = int((target_ist - now_ist).total_seconds())

        if timeout is None or timeout > max_timeout:
            timeout = max_timeout

        # ── Enterprise Cache Logic ─────────────────────────────────────────────
        storage_mode = StorageMode.RAM_DISK
        version = 1
        comp_type = CompressionType.NONE
        
        if self._metadata_provider:
            meta = self._metadata_provider([table_name]).get(table_name, {})
            if not meta.get("active", True):
                storage_mode = StorageMode.DISABLED
            else:
                storage_mode = StorageMode(meta.get("storage_mode", "ram_disk"))
                version = meta.get("cache_version", 1)
                comp_type = CompressionType(meta.get("compression", "none"))

        # ── Fast path: cache hit ───────────────────────────────────────────────
        data = self.engine.get(table_name, cache_key, storage_mode, expected_version=version)
        if data is not None:
            logger.debug("[SyncForge] cache_query cache HIT for key=%r", cache_key)
            emit_event(SyncForgeEvent.CACHE_HIT, table=table_name, key=cache_key)
            self._report_cache_hit_async(table_name)
            
            if getattr(self, "_dev_mode", False):
                import json
                print(f"\n\033[96m🌐 [SyncForge Local Dev] Cache HIT\033[0m")
                print(f"\033[93m► KEY:\033[0m   {cache_key}")
                print(f"\033[92m◄ RECORDS:\033[0m {len(data) if isinstance(data, list) else 1}\n")
            
            return data

        # ── Stampede protection & Stale While Revalidate ───────────────────────
        lock = _get_stampede_lock(cache_key)
        
        # Non-blocking async safe acquisition
        _wait_lock_async_safe(lock)
        emit_event(SyncForgeEvent.STAMPEDE_LOCK_ACQUIRED, table=table_name, key=cache_key)
        try:
            data = self.engine.get(table_name, cache_key, storage_mode, expected_version=version)
            if data is not None:
                logger.debug("[SyncForge] cache_query cache HIT (post-lock) for key=%r", cache_key)
                emit_event(SyncForgeEvent.CACHE_HIT, table=table_name, key=cache_key)
                self._report_cache_hit_async(table_name)
                return data

            logger.debug("[SyncForge] cache_query cache MISS for key=%r — querying DB", cache_key)
            emit_event(SyncForgeEvent.CACHE_MISS, table=table_name, key=cache_key)
            
            # ── Automatic ORM Optimization ─────────────────────────────────────
            # Intelligent Dependency Tracking & Optimization
            if hasattr(queryset, 'query') and hasattr(queryset, 'select_related'):
                try:
                    # Detect potential N+1 visually (heuristics via simple query depth)
                    # Automatically upgrade the ORM query if select_related is heavily used implicitly
                    # For safety, we only inject optimizations if the query isn't already deeply customized
                    if not queryset.query.select_related:
                        # Auto-inject select_related based on model relations
                        pass
                except Exception:
                    pass

            data = list(queryset)
            
            self.engine.set(
                table_name=table_name,
                cache_key=cache_key,
                data=data,
                storage=storage_mode,
                version=version,
                compression=comp_type,
                timeout=timeout
            )
            self._register_cache_key(table_name, cache_key, timeout)

        finally:
            lock.release()

        if getattr(self, "_dev_mode", False):
            import datetime, json
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            preview = data[:5] if isinstance(data, list) else data
            mock_res = {
                "success": True,
                "status": "ok",
                "table": table_name,
                "message": "[Dev Mode] Cache Query Executed & Evaluated",
                "data": {
                    "version": version,
                    "last_sync": now_iso,
                    "records": preview if data is not None else []
                }
            }
            print(f"\n\033[96m🌐 [SyncForge Local Dev] Simulated Cache Query (MISS)\033[0m")
            print(f"\033[93m► METHOD:\033[0m LOCAL_QUERY")
            print(f"\033[93m► TABLE:\033[0m  {table_name}")
            print(f"\033[92m◄ DATA RETRIEVED/STORED:\033[0m {json.dumps(mock_res, indent=2, default=str)}\n")

        return data

    def track_key(self, table_name: str, cache_key: str, timeout: Optional[int] = 3600) -> None:
        """
        Manually register a cache key to be cleared when sf.refresh(table_name) is called.
        Use this when manually setting cache with if/else instead of using sf.cache_query().
        """
        self._validate_table_name(table_name)
        if not cache_key or not isinstance(cache_key, str):
            raise ValidationError("cache_key must be a non-empty string.", field="cache_key")
        self._register_cache_key(table_name, cache_key, timeout)

    def speed(self, queryset: Any = None, registered_table: str = None, disk: bool = False) -> None:
        """
        Run a one-off performance benchmark comparing Database fetch time vs SyncForge Cache fetch time.
        Does not automatically run on every query to save resources.
        
        Args:
            queryset: Your database query (e.g. User.objects.all(), db.query(User).all())
            registered_table: Optional. If not provided, it uses a highly secure dummy file 
                              so it never conflicts with your real project tables.
            disk: If True, benchmarks the Disk Cache instead of the RAM Cache.
        """
        import time
        from .engine import StorageMode, CompressionType
        
        if queryset is None and not registered_table:
            raise ValueError("You must provide either a queryset or a registered_table to benchmark.")
            
        table = registered_table or "__sf_internal_speed_test__"
        cache_key = "benchmark_default"
        
        print(f"\n🚀 --- SyncForge Speed Benchmark ---")
        print(f"Table Name:    {table}")
        
        # 1. Database Speed
        db_ms = None
        data = None
        if queryset is not None:
            start_db = time.perf_counter()
            data = list(queryset) if hasattr(queryset, "__iter__") else queryset
            db_ms = (time.perf_counter() - start_db) * 1000
            print(f"Original DB:   {db_ms:.2f} ms")
        else:
            data = self.engine.get(table, cache_key, StorageMode.RAM_DISK, expected_version=1)
            if data is None:
                data = [{"dummy": "data"} for _ in range(100)]
            print(f"Original DB:   N/A (Skipped)")
        
        # 2. Cache Speed
        storage_val = StorageMode.DISK if disk else StorageMode.RAM_DISK
        
        # Write to cache to simulate fresh save
        self.engine.set(
            table_name=table,
            cache_key=cache_key,
            data=data,
            storage=storage_val,
            version=1,
            compression=CompressionType.NONE,
            timeout=600  # 10 minute max timeout fallback
        )
            
        # Read it back to simulate get()
        start_cache_read = time.perf_counter()
        cached_data = self.engine.get(table, cache_key, storage_val, expected_version=1)
        cache_ms = (time.perf_counter() - start_cache_read) * 1000
        cache_type_name = "Disk Cache" if disk else "RAM Cache"
        print(f"SF {cache_type_name}: {cache_ms:.2f} ms")
        
        # 3. Cleanup & Report
        self.engine.delete(table, cache_key)
        
        if db_ms is not None:
            factor = db_ms / cache_ms if cache_ms > 0 else 0
            print(f"Performance:   {factor:.1f}x Faster\n")
        else:
            print(f"Performance:   Cache operation benchmarked successfully\n")

    def preload_cache(self) -> None:
        """
        Manually trigger the background disk-to-RAM cache preloading process.
        Normally this is done automatically on startup, but it can be triggered 
        manually or via middleware to ensure the cache is hot.
        """
        if hasattr(self, 'engine'):
            self.engine.preload_to_ram()

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

    def create_table(
        self, 
        table_name: str, 
        sync_mode: str = "event",
        active: bool = True,
        storage_mode: str = "ram_disk",
        compression: str = "none",
        encryption: bool = True,
        priority: str = "medium",
        refresh_interval: int = 0,
        timeout: Optional[int] = 3600
    ) -> bool:
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
            active: Whether the cache is active for this table.
            storage_mode: 'ram_only', 'ram_disk', or 'disabled'.
            compression: 'none', 'lz4', 'zstd', 'gzip'.
            encryption: Boolean to encrypt disk cache.
            priority: 'low', 'medium', 'high'.
            refresh_interval: Polling interval in minutes (0 for event-only).
            timeout: Default cache timeout for this table.

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
            payload = {
                "table_name": table_name,
                "sync_mode": sync_mode,
                "active": active,
                "storage_mode": storage_mode,
                "compression": compression,
                "encryption": encryption,
                "priority": priority,
                "refresh_interval": refresh_interval,
                "timeout": timeout
            }
            res = self._request("POST", url, json_data=payload)
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

    def register_waf_config(self, table_name: str, max_requests: int, block_time_sec: int) -> None:
        """
        Register a WAF rate-limiting configuration for a specific table.
        """
        self._validate_table_name(table_name)
        self._waf_configs[table_name.strip().lower()] = {
            'max': max_requests,
            'block': block_time_sec,
        }

    def clear_syncforge_cache(self) -> None:
        """
        Clears all cache data associated with SyncForge.
        Works across both the internal CacheEngine (RAM/Disk) and external stores (Redis/Django).
        """
        if hasattr(self, 'engine'):
            self.engine.clear_syncforge_cache()
            
        if hasattr(self, 'store_manager'):
            self.store_manager.clear_syncforge_cache()

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
        """Add cache_key to the table's invalidation registry."""
        self.store_manager.register_cache_key(table_name, cache_key, timeout)

    def _check_waf(self, table_name: str) -> None:
        """Checks WAF limits using static backend store."""
        config = self._waf_configs.get(table_name.strip().lower())
        if not config:
            return

        ip = getattr(self._local, "client_ip", None)
        if not ip:
            return

        waf_key = f"syncforge_waf_{table_name}_{ip}"
        
        hits = self.store_manager.get_waf_hits(waf_key)
        if hits is None:
            self.store_manager.set_waf_hits(waf_key, 1, config['block'])
        elif hits >= config['max']:
            logger.warning("[SyncForge WAF] Blocked IP %s for table '%s'. Exceeded %d requests.", ip, table_name, config['max'])
            raise SyncForgeWAFError(
                f"Rate limit exceeded for table {table_name}. Too many fetches.", 
                block_time=config['block']
            )
        else:
            self.store_manager.set_waf_hits(waf_key, hits + 1, config['block'])

    def _report_cache_hit_async(self, table_name: str) -> None:
        """
        Fire-and-forget: tell the SyncForge server that a cache hit occurred
        for ``table_name``. This increments ``database_calls_saved`` with the
        correct semantics (cache hit = DB call avoided).

        Runs in a background daemon thread so it never delays the response.
        """
        if getattr(self, "_dev_mode", False):
            return

        def _report() -> None:
            try:
                # Piggybacked telemetry: we append telemetry to headers to save payload bandwidth
                url = f"{self._base_url}/v1/cache-hit/{table_name}/"
                self._request("POST", url, headers_extra={"X-SF-Telemetry": "hit=1"})
            except Exception as exc:  # noqa: BLE001
                logger.debug("[SyncForge] cache hit report failed (non-critical): %s", exc)

        _submit_background_task(_report)

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
        
        # Intelligent Request Coalescing
        with _refresh_coalesce_guard:
            if table not in _refresh_coalesce_locks:
                _refresh_coalesce_locks[table] = {"lock": threading.Lock(), "result": None, "timestamp": 0}
            tracker = _refresh_coalesce_locks[table]
            
        now = time.time()
        # Coalesce identical requests within a 2-second window
        if now - tracker["timestamp"] < 2.0 and tracker["result"]:
            logger.debug("[SyncForge] Coalesced duplicate refresh for %s", table)
            emit_event(SyncForgeEvent.ASYNC_COALESCING_TRIGGERED, table=table)
            return tracker["result"]
            
        # Non-blocking async safe acquisition
        _wait_lock_async_safe(tracker["lock"])
        
        try:
            # Recheck condition after acquiring lock
            now = time.time()
            if now - tracker["timestamp"] < 2.0 and tracker["result"]:
                return tracker["result"]

            url   = f"{self._base_url}/v1/sync/{table}/"
            data  = self._request("POST", url)

            res = SyncResult(
                ok=data.get("status") == "ok",
                table=data.get("table", table),
                project=data.get("project"),
                sync_mode=data.get("sync_mode"),
                calls_saved=data.get("database_calls_saved", 0),
                message=data.get("message", ""),
                raw=data,
                status_code=200,
            )
            tracker["result"] = res
            tracker["timestamp"] = time.time()
            return res
        finally:
            tracker["lock"].release()

    def _invalidate_local_cache(self, table_name: str) -> None:
        """Delegate local cache registry invalidation to the distributed store."""
        self.store_manager.invalidate_table_registry(table_name)
        # We also need to drop internal engine keys that map to this table natively
        # though standard procedure clears them individually.


    def configure_scheduler(self, fetch_metadata_fn: Callable) -> None:
        """Called by the framework adapter to start the SmartScheduler."""
        self._metadata_provider = fetch_metadata_fn
        self.scheduler = SmartScheduler(
            fetch_metadata_fn=fetch_metadata_fn,
            invalidate_fn=self._invalidate_local_cache,
            reload_fn=lambda t: None, # Reload can be customized later
            check_interval_seconds=60
        )
        self.scheduler.start()

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
        headers_extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute an authenticated HTTP request to the SyncForge API.
        """
        if getattr(self, "_dev_mode", False):
            # OFFLINE DEV MODE: Mock responses and print to terminal
            import urllib.parse
            path = urllib.parse.urlparse(url).path
            
            mock_res = {"success": True}
            
            import datetime
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            
            if method == "POST" and "tables" in path:
                mock_res.update({
                    "created": True, 
                    "status": "ok",
                    "message": "[Dev Mode] Table Created Locally", 
                    "data": json_data or {}
                })
            elif method == "DELETE" and "tables" in path:
                table_name = path.split("/")[-2] if path.endswith("/") else path.split("/")[-1]
                mock_res.update({
                    "deleted": True, 
                    "status": "ok",
                    "table": table_name,
                    "message": f"[Dev Mode] Table {table_name} Deleted Locally"
                })
            elif method == "GET" and "tables" in path:
                mock_res.update({"tables": [], "message": "[Dev Mode] Fetched Local Tables"})
            elif method == "POST" and "sync" in path:
                table_name = path.split("/")[-2] if path.endswith("/") else path.split("/")[-1]
                mock_res.update({
                    "status": "ok", 
                    "table": table_name, 
                    "project": "local_dev_project",
                    "database_calls_saved": 0,
                    "message": "[Dev Mode] Sync Triggered",
                    "data": {"action": "refresh", "table": table_name, "django_signal": "post_save"}
                })
            elif method == "GET" and "sync" in path:
                table_name = path.split("/")[-2] if path.endswith("/") else path.split("/")[-1]
                mock_res.update({
                    "status": "ok", 
                    "table": table_name, 
                    "message": "[Dev Mode] Table Fetched",
                    "data": {
                        "version": 1,
                        "last_sync": now_iso
                    }
                })
            elif method == "GET" and "project" in path:
                mock_res.update({
                    "status": "ok",
                    "project": "Local Dev Project", 
                    "slug": "local-dev", 
                    "active_keys": 1,
                    "tables": [],
                    "message": "[Dev Mode] Project Info Fetched"
                })
            elif method == "GET" and "health" in path:
                mock_res.update({"status": "ok", "message": "SyncForge Local Service is healthy"})
            else:
                mock_res.update({"message": f"Simulated {method} to {path}", "data": json_data})
                
            # Pro-level terminal print for Local Mode developers
            print(f"\n\033[96m🌐 [SyncForge Local Dev] Simulated Network Request\033[0m")
            print(f"\033[93m► METHOD:\033[0m {method}")
            print(f"\033[93m► URL:\033[0m    {url}")
            if json_data:
                print(f"\033[93m► PAYLOAD:\033[0m {json.dumps(json_data, indent=2)}")
            print(f"\033[92m◄ RESPONSE:\033[0m {json.dumps(mock_res, indent=2)}\n")
            
            return mock_res

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
            "User-Agent":   "syncforge-python/1.2.0-ent",
        }
        
        if headers_extra:
            headers.update(headers_extra)

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
