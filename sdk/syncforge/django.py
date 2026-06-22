"""
SyncForge — Django Integration
================================

Provides the ``@sync_model`` decorator and ``sync_migrations`` utility for
seamless Django integration.

``@sync_model`` hooks into Django's ORM signal system (``post_save`` /
``post_delete``) to automatically:

1. Invalidate the local cache for the changed table.
2. Send a refresh signal to the SyncForge server (asynchronously — non-blocking).

The signal handler runs entirely in a background daemon thread so it never
delays the request/response cycle or blocks within a database transaction.

Example::

    # models.py
    from django.db import models
    from syncforge import sf
    from syncforge.django import sync_model

    @sync_model(sf, sync_mode='event')
    class Product(models.Model):
        name  = models.CharField(max_length=200)
        price = models.DecimalField(max_digits=10, decimal_places=2)

        class Meta:
            db_table = 'core_product'
"""
from __future__ import annotations

import logging
import threading
from typing import Set, Type

logger = logging.getLogger("syncforge.django")

# ── State ──────────────────────────────────────────────────────────────────────

_registered_tables: Set[str] = set()
_registration_lock = threading.Lock()

try:
    from django.db import models
    from django.db.models.signals import post_save, post_delete  # type: ignore[import]
    from django.apps import apps  # type: ignore[import]
    HAS_DJANGO = True
except ImportError:
    HAS_DJANGO = False

# ── Metadata Model ─────────────────────────────────────────────────────────────

if HAS_DJANGO:
    class SyncforgeMetadata(models.Model):
        """
        Internal table managed by SyncForge to track what is cached locally,
        when it was last accessed, its size on disk/RAM, and overall status.
        """
        table_name = models.CharField(max_length=255, unique=True, primary_key=True)
        storage_mode = models.CharField(max_length=50, default="ram_disk")
        compression = models.CharField(max_length=50, default="none")
        encryption = models.BooleanField(default=False)
        cache_version = models.BigIntegerField(default=1)
        last_accessed = models.DateTimeField(auto_now=True)
        size_bytes = models.BigIntegerField(default=0)
        status = models.CharField(max_length=50, default="active")

        class Meta:
            db_table = "syncforge_metadata"
            verbose_name_plural = "Syncforge Metadata"

    def update_local_metadata(table_name: str, **kwargs):
        """Helper to safely update the local metadata table without triggering signals."""
        try:
            SyncforgeMetadata.objects.update_or_create(
                table_name=table_name,
                defaults=kwargs
            )
        except Exception as e:
            logger.debug("[SyncForge] Failed to update local metadata: %s", e)

    def fetch_django_metadata(table_names: List[str]) -> Dict[str, dict]:
        """Callable for SmartScheduler to fetch local metadata configs."""
        try:
            records = SyncforgeMetadata.objects.filter(table_name__in=table_names)
            return {
                r.table_name: {
                    "storage_mode": r.storage_mode,
                    "compression": r.compression,
                    "encryption": r.encryption,
                    "cache_version": r.cache_version,
                    "active": r.status == "active"
                }
                for r in records
            }
        except Exception as e:
            logger.debug("[SyncForge] Failed to fetch local metadata: %s", e)
            return {}


# ── Decorator ─────────────────────────────────────────────────────────────────

def sync_model(
    sf_client, 
    sync_mode: str = "event",
    active: bool = True,
    storage_mode: str = "ram_disk",
    compression: str = "none",
    encryption: bool = True,
    priority: str = "medium",
    refresh_interval: int = 0,
    waf_enabled: bool = False, 
    max_requests: int = 100, 
    block_time_sec: int = 86400
):
    """
    Class decorator for Django models that enables automatic SyncForge
    synchronisation.

    When a decorated model is saved or deleted, SyncForge will:

    * Immediately invalidate any cache entries registered under this table's
      name (via the invalidation registry populated by ``cache_query``).
    * Asynchronously notify the SyncForge server so connected clients are
      informed of the change.

    The HTTP call to the SyncForge server runs in a daemon thread and never
    blocks the caller. If the call fails, it is logged as an error; it does
    **not** raise an exception or affect the database write.

    Args:
        sf_client:
            A configured :class:`~syncforge.client.SyncForge` instance.
        sync_mode:
            The sync mode to register on the SyncForge dashboard.
            One of ``'event'``, ``'manual'``, ``'schedule_5m'``,
            ``'schedule_1h'``, ``'schedule_1d'``, ``'schedule_30d'``,
            ``'hybrid'``. Default: ``'event'``.
        waf_enabled:
            Enable the Anti-DDoS Web Application Firewall (WAF) for this table.
            Default: ``False``.
        max_requests:
            Maximum number of database fetches allowed per IP for this table.
            Default: ``100``.
        block_time_sec:
            Number of seconds to block the IP if `max_requests` is exceeded.
            Default: ``86400`` (1 day).

    Returns:
        The original model class, unmodified — this decorator is transparent
        to Django's model system.

    Raises:
        :class:`ImportError`: If Django is not installed.

    Example::

        from syncforge import sf
        from syncforge.django import sync_model

        @sync_model(sf, sync_mode='event')
        class Product(models.Model):
            name = models.CharField(max_length=200)
    """
    def decorator(cls: Type) -> Type:
        if not HAS_DJANGO:
            raise ImportError(
                "Django is not installed. The @sync_model decorator requires Django."
            )

        table_name: str = cls._meta.db_table
        
        if waf_enabled:
            sf_client.register_waf_config(table_name, max_requests, block_time_sec)

        with _registration_lock:
            if table_name in _registered_tables:
                # Idempotent — already registered (e.g. AppConfig.ready() called twice).
                logger.debug(
                    "[SyncForge] @sync_model: '%s' already registered — skipping.",
                    table_name,
                )
                return cls

            # ── 1. Register table on the SyncForge dashboard ─────────────────
            # This is a network call that happens at class-definition time
            # (usually during Django startup / AppConfig.ready()). We attempt
            # it but never let it crash the import.
            try:
                sf_client.create_table(
                    table_name, 
                    sync_mode=sync_mode,
                    active=active,
                    storage_mode=storage_mode,
                    compression=compression,
                    encryption=encryption,
                    priority=priority,
                    refresh_interval=refresh_interval
                )
                update_local_metadata(
                    table_name,
                    storage_mode=storage_mode,
                    compression=compression,
                    encryption=encryption,
                    status="active" if active else "inactive"
                )
                logger.info("[SyncForge] Registered table '%s' (mode=%s).", table_name, sync_mode)
                print(f"\033[92m🚀 [SyncForge] Successfully registered table '{table_name}' (mode={sync_mode}, storage={storage_mode}, enc={encryption})\033[0m")
            except Exception as exc:
                logger.warning(
                    "[SyncForge] Could not register table '%s' on SyncForge server: %s. "
                    "Local cache invalidation will still work correctly.",
                    table_name, exc,
                )
                print(f"\033[91m❌ [SyncForge] Failed to register table '{table_name}': {exc}\033[0m")

            # ── 2. Connect ORM signals ────────────────────────────────────────
            _connect_signals(sf_client, cls, table_name)

            _registered_tables.add(table_name)
            logger.debug("[SyncForge] Signal hooks installed for table '%s'.", table_name)

        return cls
    return decorator


def _connect_signals(sf_client, model_cls: Type, table_name: str) -> None:
    """
    Connect ``post_save`` and ``post_delete`` signals for ``model_cls``.

    The ``dispatch_uid`` parameter ensures each signal handler is registered
    exactly once, even if this function is called multiple times (e.g., during
    Django's app-ready cycle with autoreload).
    """
    def _trigger_sync(sender, **kwargs) -> None:  # noqa: ANN001
        """
        Signal handler — runs synchronously in the caller's thread.

        It immediately invalidates the local cache (fast, in-process) and
        then spawns a daemon thread for the network call to the SyncForge
        server (slow, non-blocking).
        """
        # ── Step 1: Invalidate local cache (fast, synchronous) ────────────────
        _invalidate_local_cache(table_name)

        # ── Step 2: Notify SyncForge server (slow, async) ────────────────────
        thread = threading.Thread(
            target=_notify_server,
            args=(sf_client, table_name),
            daemon=True,
            name=f"sf-sync-{table_name}",
        )
        thread.start()

    post_save.connect(
        _trigger_sync,
        sender=model_cls,
        weak=False,
        dispatch_uid=f"sf_save_{table_name}",
    )
    post_delete.connect(
        _trigger_sync,
        sender=model_cls,
        weak=False,
        dispatch_uid=f"sf_delete_{table_name}",
    )


def _invalidate_local_cache(table_name: str) -> None:
    """
    Delete all cache entries registered under ``table_name``'s invalidation
    registry. This is the fast, synchronous part of the sync handler.
    """
    try:
        from django.core.cache import cache  # type: ignore[import]
        registry_key = f"sf_registry_{table_name}"
        keys: set = cache.get(registry_key) or set()
        if keys:
            cache.delete_many(list(keys))
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


def _notify_server(sf_client, table_name: str) -> None:
    """
    Send a refresh signal to the SyncForge server. Runs in a daemon thread.

    Errors are logged but never re-raised — a SyncForge service interruption
    must never affect the application's database write path.
    """
    try:
        sf_client.refresh(table_name)
        logger.debug("[SyncForge] Server notified of change in table '%s'.", table_name)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[SyncForge] Failed to notify server of change in table '%s': %s. "
            "Local cache has already been invalidated.",
            table_name, exc,
        )


# ── Migration Cleanup ──────────────────────────────────────────────────────────

def sync_migrations(sf_client) -> None:
    """
    Remove tables from the SyncForge dashboard that no longer exist in your
    Django project.

    Call this inside ``AppConfig.ready()`` or after running migrations to keep
    the dashboard in sync with your actual model set. It is safe to call
    repeatedly — it only deletes tables that are registered on SyncForge but
    absent from the current Django model registry.

    Args:
        sf_client:
            A configured :class:`~syncforge.client.SyncForge` instance.

    Example::

        # apps.py
        from django.apps import AppConfig

        class MyAppConfig(AppConfig):
            name = 'myapp'

            def ready(self):
                from syncforge import sf
                from syncforge.django import sync_migrations
                sync_migrations(sf)
    """
    if not HAS_DJANGO:
        logger.warning("[SyncForge] sync_migrations called but Django is not installed.")
        return

    try:
        active_db_tables: Set[str] = {
            model._meta.db_table for model in apps.get_models()
        }

        registered_tables = sf_client.list_tables()
        removed = 0
        for entry in registered_tables:
            table_name = entry.get("table_name", "")
            if table_name and table_name not in active_db_tables:
                try:
                    sf_client.delete_table(table_name)
                    logger.info("[SyncForge] Removed stale table '%s' from dashboard.", table_name)
                    removed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[SyncForge] Could not remove stale table '%s': %s",
                        table_name, exc,
                    )

        if removed:
            logger.info("[SyncForge] sync_migrations: removed %d stale table(s).", removed)
        else:
            logger.debug("[SyncForge] sync_migrations: no stale tables found.")

    except Exception as exc:  # noqa: BLE001
        logger.warning("[SyncForge] sync_migrations failed: %s", exc)


# ── Middleware ─────────────────────────────────────────────────────────────────

class SyncForgePreloadMiddleware:
    """
    Middleware to automatically preload the SyncForge cache from Disk to RAM 
    on the first request to the application.
    
    This ensures that before a user hits a cache_query() execution path, the 
    encrypted disk payloads are already decompressed and ready in RAM for 
    zero-latency reads.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import sys
        # Trigger preload on any instantiated SyncForge client in sys.modules
        for mod_name, mod in list(sys.modules.items()):
            sf_client = getattr(mod, 'sf', None)
            if sf_client and hasattr(sf_client, 'preload_cache'):
                sf_client.preload_cache()
                
        return self.get_response(request)

class SyncForgeWAFMiddleware:
    """
    SyncForge Anti-DDoS Web Application Firewall (WAF) Middleware.

    Extracts the client IP from the incoming request and attaches it to the 
    SyncForge thread-local storage. If the user exceeds the rate limit 
    defined in ``@sync_model(waf_enabled=True)``, this middleware intercepts 
    the exception and returns a 429 Too Many Requests response.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def _get_client_ip(self, request) -> str:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')

    def __call__(self, request):
        from django.http import JsonResponse
        from syncforge.exceptions import SyncForgeWAFError
        
        # We need the global 'sf' client instance used by the project.
        # Since the user initializes 'sf' in their sf_client.py, we try to 
        # find the thread-local storage dynamically if multiple sf clients exist, 
        # or we just rely on standard monkeypatching. 
        # Safest approach: find any SyncForge instances tracking IPs, or if the user 
        # imports this middleware, we assume they have 'sf'.
        # For a robust SDK, we can look up the syncforge logger or simply 
        # provide a generic way to set the IP for all clients.
        import sys
        
        ip = self._get_client_ip(request)
        
        # Inject IP into any SyncForge clients loaded in sys.modules
        # (This is a robust way to avoid forcing the user to pass 'sf' to the middleware)
        sf_clients_found = []
        for mod_name, mod in list(sys.modules.items()):
            if getattr(mod, 'sf', None) and hasattr(mod.sf, '_local'):
                sf_clients_found.append(mod.sf)
                mod.sf._local.client_ip = ip

        try:
            response = self.get_response(request)
            return response
        except SyncForgeWAFError as e:
            return JsonResponse({
                "error": "Rate limit exceeded. Too many requests.",
                "blocked_for_seconds": e.block_time
            }, status=429)
        finally:
            # Clean up thread-local storage to prevent memory leaks in worker threads
            for client in sf_clients_found:
                client._local.client_ip = None
