"""
SyncForge REST API — v1
========================

Endpoints:

* ``GET  /api/v1/health/``                      — Public health check.
* ``GET  /api/v1/project/``                     — Project metadata.
* ``GET  /api/v1/tables/``                      — List registered tables.
* ``POST /api/v1/tables/``                      — Register a table.
* ``DELETE /api/v1/tables/``                    — Remove a table.
* ``POST /api/v1/sync/<table_name>/``           — Signal a data refresh.
* ``POST /api/v1/cache-hit/<table_name>/``      — Record a cache hit (internal SDK).

Authentication: ``X-API-Key`` header (required for all endpoints except health).

Security:
* Rate limiting applied to ``/sync/`` endpoints (60 req/min per project).
* All counter increments use ``F()`` expressions for atomic, race-free updates.
* Table names are validated against a strict character allowlist.
* All sync events are logged to the ``SyncEvent`` audit table.
"""
from __future__ import annotations

import json
import logging
import re

from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from dashboard.models import TableSyncConfig, SyncEvent
from .rate_limit import rate_limit

logger = logging.getLogger("syncforge.api")

# Strict allowlist: only lowercase letters, digits, and underscores.
# Must start with a letter or underscore.
_TABLE_NAME_RE = re.compile(r'^[a-z_][a-z0-9_]{0,254}$')


def _json(data: dict, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status)


def _validate_table_name(table_name: str) -> tuple[bool, str]:
    """
    Validate a table name against the strict allowlist.

    Returns:
        ``(True, '')`` if valid.
        ``(False, error_message)`` if invalid.
    """
    if not table_name:
        return False, "table_name is required."
    normalised = table_name.strip().lower()
    if not _TABLE_NAME_RE.match(normalised):
        return False, (
            f"Invalid table name '{table_name}'. "
            "Table names must start with a letter or underscore and contain "
            "only lowercase letters, digits, and underscores."
        )
    return True, ""


def _log_sync_event(
    project,
    table_config,
    action: str = "refresh",
    status: str = "ok",
    error_message: str = "",
    affected_ids=None,
) -> None:
    """Write a SyncEvent audit record (non-fatal — errors are logged, not raised)."""
    try:
        SyncEvent.objects.create(
            project=project,
            table_config=table_config,
            action=action,
            status=status,
            error_message=error_message,
            affected_ids=affected_ids,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SyncForge] Failed to write SyncEvent: %s", exc)


# ── Endpoints ──────────────────────────────────────────────────────────────────

def health(request):
    """
    GET /api/v1/health/

    Public health check — no authentication required.
    Returns service status and API version.
    """
    return _json({
        "status":  "ok",
        "service": "SyncForge API",
        "version": "1.1",
    })


def project_info(request):
    """
    GET /api/v1/project/

    Returns project metadata, registered tables, and active API key count
    for the project associated with the provided API key.

    Authentication: X-API-Key required.
    """
    project = getattr(request, "api_project", None)
    if not project:
        return _json({"error": "X-API-Key header required for project info."}, 400)

    tables = list(project.table_configs.values(
        "table_name", "sync_mode", "rows_count",
        "database_calls_saved", "bandwidth_saved_mb", "duplicates_prevented",
        "cache_hits", "cache_misses", "total_requests",
        "cache_version", "last_sync",
        "active", "storage_mode", "compression", "encryption", "priority", "refresh_interval"
    ))
    return _json({
        "project":     project.name,
        "slug":        project.slug,
        "tables":      tables,
        "active_keys": project.api_keys.filter(is_active=True).count(),
    })


@csrf_exempt
@rate_limit(requests_per_minute=60)
def smartdb_refresh(request, table_name: str):
    """
    POST /api/v1/sync/<table_name>/

    Signal that data in ``table_name`` has changed. SyncForge records the
    event, increments the table's version number, and returns updated stats.

    This endpoint is called by ``sf.refresh('table_name')`` in the SDK.

    Authentication: X-API-Key required.
    Rate limit: 60 requests/minute per project.
    """
    if request.method not in ("POST", "GET"):
        return _json({"error": "Method not allowed. Use POST."}, 405)

    project = getattr(request, "api_project", None)
    user    = getattr(request, "api_user", None)

    # ── Validate table name ───────────────────────────────────────────────────
    valid, err = _validate_table_name(table_name)
    if not valid:
        return _json({"error": err}, 400)

    normalised = table_name.strip().lower()

    if project:
        # Enforce that the table must already be registered in the project dashboard
        config = TableSyncConfig.objects.filter(project=project, table_name=normalised).first()
        if not config:
            error_msg = f"Error: Attempted to fetch or refresh unregistered table '{normalised}'."
            from dashboard.models import ProjectLog
            if not ProjectLog.objects.filter(project=project, event_type='error', details=error_msg).exists():
                ProjectLog.objects.create(
                    project=project,
                    event_type='error',
                    details=error_msg
                )
            
            return _json({
                "error": f"Table '{normalised}' is not registered in this project. Please register it in the SyncForge dashboard first."
            }, 404)
            
        created_new = False

        # Cooldown rate limit check: Server-Side Request Coalescing
        COOLDOWN_SECONDS = 60
        if not created_new and config.last_sync:
            elapsed = (timezone.now() - config.last_sync).total_seconds()
            if elapsed < COOLDOWN_SECONDS:
                # Instead of HTTP 429 Error, Coalesce the request!
                return _json({
                    "status": "ok",
                    "message": f"Sync coalesced for table `{normalised}` (within cooldown).",
                    "table": normalised,
                    "scoped_table_name": f"sf_{project.project_prefix or 'default'}_{normalised}",
                    "project": project.name,
                    "sync_mode": config.get_sync_mode_display(),
                    "database_calls_saved": config.database_calls_saved,
                    "cache_version": config.cache_version,
                    "coalesced": True
                })

        # Atomic update — no lost-update race condition under concurrency.
        # F() expressions are evaluated by the database, not Python.
        TableSyncConfig.objects.filter(pk=config.pk).update(
            cache_version=F("cache_version") + 1,
            last_sync=timezone.now(),
        )
        config.refresh_from_db(fields=["cache_version", "database_calls_saved"])

        _log_sync_event(project, config, action="refresh", status="ok")
        logger.info(
            "[SyncForge] Refresh: project='%s' table='%s' version=%d",
            project.slug, normalised, config.cache_version,
        )

        project_prefix = project.project_prefix or "default"
        return _json({
            "status":               "ok",
            "message":              f"Sync triggered and table auto-registered for `{normalised}`." if created_new else f"Sync triggered for table `{normalised}`.",
            "table":                normalised,
            "scoped_table_name":    f"sf_{project_prefix}_{normalised}",
            "project":              project.name,
            "sync_mode":            config.get_sync_mode_display(),
            "database_calls_saved": config.database_calls_saved,
            "cache_version":        config.cache_version,
        })

    elif user:
        # JWT / session auth — no project context; no stats tracked.
        return _json({
            "status":  "ok",
            "message": f"Sync triggered for `{normalised}`.",
            "hint":    "Use an API key (X-API-Key header) for project-scoped statistics.",
        })

    return _json({"error": "Unauthenticated."}, 401)


@csrf_exempt
def cache_hit_report(request, table_name: str):
    """
    POST /api/v1/cache-hit/<table_name>/

    Internal endpoint called by the SDK whenever a ``cache_query`` cache hit
    occurs. Atomically increments ``database_calls_saved`` for the table.

    This is the correct place to increment the counter — a cache hit means
    one database read was avoided. Intentionally minimal: no rate limiting
    (SDK calls are infrequent fire-and-forget) and no SyncEvent written
    (would generate excessive audit records).

    Authentication: X-API-Key required.
    """
    if request.method != "POST":
        return _json({"error": "POST required."}, 405)

    project = getattr(request, "api_project", None)
    if not project:
        return _json({"error": "X-API-Key required."}, 401)

    valid, err = _validate_table_name(table_name)
    if not valid:
        return _json({"error": err}, 400)

    normalised = table_name.strip().lower()

    updated = TableSyncConfig.objects.filter(
        project=project, table_name=normalised
    ).update(
        database_calls_saved=F("database_calls_saved") + 1,
    )

    if updated:
        return _json({"status": "ok", "table": normalised})

    # Table not registered — log it so the developer knows there's a configuration issue.
    error_msg = f"Error: Attempted to fetch or refresh unregistered table '{normalised}'."
    from dashboard.models import ProjectLog
    if not ProjectLog.objects.filter(project=project, event_type='error', details=error_msg).exists():
        ProjectLog.objects.create(
            project=project,
            event_type='error',
            details=error_msg
        )

    return _json({"error": f"Table '{normalised}' is not registered."}, 404)


@csrf_exempt
def tables_list(request):
    """
    GET    /api/v1/tables/   — List all registered tables for the project.
    POST   /api/v1/tables/   — Register a new table.
    DELETE /api/v1/tables/   — Remove a table (by ``table_name`` query param or body).

    Authentication: X-API-Key required.
    """
    project = getattr(request, "api_project", None)
    if not project:
        return _json({"error": "X-API-Key required."}, 400)

    # ── POST: Register a table ────────────────────────────────────────────────
    if request.method == "POST":
        try:
            if len(request.body) > 8192:
                return _json({"error": "Payload too large. Maximum size is 8KB."}, 413)
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _json({"error": "Request body must be valid JSON."}, 400)

        table_name = body.get("table_name", "")
        sync_mode  = body.get("sync_mode", "event")
        active = body.get("active", True)
        storage_mode = body.get("storage_mode", "ram_disk")
        compression = body.get("compression", "none")
        encryption = body.get("encryption", False)
        priority = body.get("priority", "medium")
        refresh_interval = body.get("refresh_interval", 0)
        timeout = body.get("timeout", None)

        valid, err = _validate_table_name(table_name)
        if not valid:
            return _json({"error": err}, 400)

        normalised = table_name.strip().lower()

        valid_modes = dict(TableSyncConfig.SYNC_MODES)
        if sync_mode not in valid_modes:
            return _json({
                "error": f"Invalid sync_mode '{sync_mode}'.",
                "valid_modes": list(valid_modes.keys()),
            }, 400)

        config, created = project.table_configs.get_or_create(
            table_name=normalised,
            defaults={
                "sync_mode": sync_mode,
                "active": active,
                "storage_mode": storage_mode,
                "compression": compression,
                "encryption": encryption,
                "priority": priority,
                "refresh_interval": refresh_interval,
                "timeout": timeout
            },
        )
        if not created:
            updated = False
            if config.sync_mode != sync_mode:
                config.sync_mode = sync_mode
                updated = True
            if config.active != active:
                config.active = active
                updated = True
            if config.storage_mode != storage_mode:
                config.storage_mode = storage_mode
                updated = True
            if config.compression != compression:
                config.compression = compression
                updated = True
            if config.encryption != encryption:
                config.encryption = encryption
                updated = True
            if config.priority != priority:
                config.priority = priority
                updated = True
            if config.refresh_interval != refresh_interval:
                config.refresh_interval = refresh_interval
                updated = True
            if config.timeout != timeout:
                config.timeout = timeout
                updated = True
                
            if updated:
                config.save(update_fields=[
                    "sync_mode", "active", "storage_mode", "compression",
                    "encryption", "priority", "refresh_interval", "timeout"
                ])

        project_prefix = project.project_prefix or "default"
        return _json({
            "status":            "ok",
            "table_name":        normalised,
            "scoped_table_name": f"sf_{project_prefix}_{normalised}",
            "sync_mode":         config.sync_mode,
            "storage_mode":      config.storage_mode,
            "created":           created,
        })

    # ── DELETE: Remove a table ────────────────────────────────────────────────
    if request.method == "DELETE":
        table_name = request.GET.get("table_name", "").strip()
        if not table_name:
            try:
                if len(request.body) > 8192:
                    return _json({"error": "Payload too large. Maximum size is 8KB."}, 413)
                body = json.loads(request.body)
                table_name = body.get("table_name", "").strip()
            except Exception:  # noqa: BLE001
                pass

        valid, err = _validate_table_name(table_name)
        if not valid:
            return _json({"error": err}, 400)

        normalised = table_name.strip().lower()
        deleted, _ = project.table_configs.filter(table_name=normalised).delete()
        return _json({"status": "ok", "deleted": deleted > 0, "table_name": normalised})

    # ── GET: List all tables ──────────────────────────────────────────────────
    tables = []
    project_prefix = project.project_prefix or "default"
    for t in project.table_configs.all():
        tables.append({
            "table_name": t.table_name,
            "scoped_table_name": f"sf_{project_prefix}_{t.table_name}",
            "sync_mode": t.sync_mode,
            "rows_count": t.rows_count,
            "database_calls_saved": t.database_calls_saved,
            "cache_version": t.cache_version,
            "last_sync": t.last_sync.isoformat() if t.last_sync else None,
            "active": t.active,
            "storage_mode": t.storage_mode,
            "compression": t.compression,
            "encryption": t.encryption,
            "priority": t.priority,
            "refresh_interval": t.refresh_interval,
            "timeout": t.timeout,
        })
    return _json({"tables": tables, "count": len(tables)})
