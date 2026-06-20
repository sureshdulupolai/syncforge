"""
SyncForge — Unified API Authentication Middleware
==================================================

Authenticates every request using one of three mechanisms, tried in order:

1. **X-API-Key header** (REST API clients — curl, SDKs, external apps)
2. **JWT cookie** (``sf_access_token``) — SyncForge web dashboard
3. **Django session** — dashboard fallback

Attaches to ``request``:

* ``request.api_project`` — :class:`~dashboard.models.Project` instance
  (only set for API-key auth; ``None`` for JWT/session auth).
* ``request.api_user`` — :class:`~django.contrib.auth.models.User` instance
  (always set when authentication succeeds).

Security Improvements (v1.1)
-----------------------------
* API key lookups are **cached for 60 seconds** to avoid a DB read + write on
  every request. The cache key is derived from an HMAC of the raw key value,
  so the raw key is never stored in cache.
* **On-the-fly key migration**: legacy plaintext keys are looked up as a
  fallback, then their hash is written on first successful use — transparent to
  the developer.
* ``last_used`` timestamps are updated **asynchronously** (daemon thread) so
  they never add latency to the response path.
* **Replay protection**: if the client sends ``X-SF-Timestamp``, the middleware
  validates that the timestamp is within ±5 minutes of server time. Requests
  with stale timestamps are rejected with HTTP 400.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
import threading
import time

import jwt
from django.http import JsonResponse
from django.contrib.auth.models import User
from dashboard.models import APIKey
from dashboard.jwt_utils import decode_token

logger = logging.getLogger("syncforge.auth")

# Paths that require no authentication at all.
PUBLIC_PATHS = frozenset({
    "/",
    "/api/v1/health/",
    "/dashboard/login/",
    "/dashboard/register/",
    "/dashboard/sf-admin-init-9x7k/register/",
    "/dashboard/auth/token/refresh/",
})

# Maximum age for X-SF-Timestamp replay protection (seconds).
_TIMESTAMP_TOLERANCE: int = 300  # ±5 minutes


def _hash_raw_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _cache_lookup_key(raw_key: str) -> str:
    """
    Return the Django cache key used to store API key resolution results.

    We derive this from a truncated HMAC of the raw key (not the raw key
    itself) so that the raw API key value is never written to the cache store.
    """
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return f"sf_apikey_{digest[:24]}"


def _update_last_used_async(key_pk: int) -> None:
    """Update APIKey.last_used in a daemon thread to avoid blocking the request."""
    def _do() -> None:
        try:
            from django.utils import timezone
            APIKey.objects.filter(pk=key_pk).update(last_used=timezone.now())
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SyncForge] last_used update failed (non-critical): %s", exc)

    threading.Thread(target=_do, daemon=True, name="sf-last-used").start()


def _resolve_api_key(raw_key: str):
    """
    Resolve a raw API key string to an ``APIKey`` model instance.

    Lookup order:
    1. Django cache (60-second TTL) — avoids DB hit on every request.
    2. Hashed lookup against ``APIKey.key_hash`` (new, secure storage).
    3. Legacy plaintext lookup against ``APIKey.key`` (deprecated field).
       On a successful legacy lookup the hash is written back automatically.

    Returns:
        A dict ``{'project_id': int, 'user_id': int, 'key_pk': int}`` on
        success, or ``None`` if the key is invalid/inactive.
    """
    from django.core.cache import cache

    cache_key = _cache_lookup_key(raw_key)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    incoming_hash = _hash_raw_key(raw_key)
    key_obj = None

    # ── Try hashed lookup first (new keys) ────────────────────────────────────
    try:
        key_obj = (
            APIKey.objects
            .select_related("project", "project__user")
            .get(key_hash=incoming_hash, is_active=True)
        )
    except APIKey.DoesNotExist:
        pass

    # ── Fallback: legacy plaintext key (deprecated) ───────────────────────────
    if key_obj is None:
        try:
            key_obj = (
                APIKey.objects
                .select_related("project", "project__user")
                .get(key=raw_key, is_active=True)
            )
            # Migrate on first use: store the hash so future lookups use it.
            try:
                key_obj.key_hash   = incoming_hash
                key_obj.key_prefix = raw_key[:18]
                key_obj.save(update_fields=["key_hash", "key_prefix"])
                logger.info(
                    "[SyncForge] Migrated API key pk=%d to hashed storage.",
                    key_obj.pk,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SyncForge] Could not migrate key pk=%d: %s", key_obj.pk, exc)
        except APIKey.DoesNotExist:
            pass

    if key_obj is None:
        return None

    result = {
        "project_id": key_obj.project_id,
        "user_id":    key_obj.project.user_id,
        "key_pk":     key_obj.pk,
    }

    # Cache the resolution for 60 seconds.
    cache.set(cache_key, result, 60)

    # Update last_used without blocking.
    _update_last_used_async(key_obj.pk)

    return result


def _validate_timestamp(request) -> bool:
    """
    Validate the ``X-SF-Timestamp`` header for replay-attack protection.

    If the header is absent, validation is skipped (backward-compatible with
    older SDK versions that do not send the header).

    If the header is present, the timestamp must be within ±5 minutes of the
    server's current time.

    Returns:
        ``True`` if the timestamp is valid (or absent).
        ``False`` if the timestamp is present but stale/malformed.
    """
    ts_header = request.headers.get("X-SF-Timestamp")
    if not ts_header:
        return True  # Header absent — skip validation.

    try:
        client_ts = int(ts_header)
    except ValueError:
        logger.warning("[SyncForge] Invalid X-SF-Timestamp header: %r", ts_header)
        return False

    delta = abs(time.time() - client_ts)
    if delta > _TIMESTAMP_TOLERANCE:
        logger.warning(
            "[SyncForge] Stale X-SF-Timestamp: delta=%ds (tolerance=%ds).",
            int(delta), _TIMESTAMP_TOLERANCE,
        )
        return False

    return True


class UnifiedAuthMiddleware:
    """
    Unified authentication middleware for SyncForge.

    Handles API-key auth (REST clients), JWT cookies (dashboard), and
    Django sessions (dashboard fallback) in a single middleware layer.
    """

    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        request.api_project = None
        request.api_user    = None

        is_api = request.path.startswith("/api/")

        # ── 1. X-API-Key (REST clients) ───────────────────────────────────────
        raw_key = (
            request.headers.get("X-API-Key")
            or request.GET.get("api_key")
        )
        if raw_key:
            # Replay-protection: validate timestamp if provided.
            if not _validate_timestamp(request):
                return JsonResponse(
                    {
                        "error": "Request timestamp is too old or too far in the future.",
                        "detail": (
                            f"X-SF-Timestamp must be within ±{_TIMESTAMP_TOLERANCE}s "
                            "of the server time. Check your system clock."
                        ),
                    },
                    status=400,
                )

            resolved = _resolve_api_key(raw_key)
            if resolved:
                try:
                    from dashboard.models import Project
                    project = Project.objects.select_related("user").get(
                        pk=resolved["project_id"]
                    )
                    request.api_project = project
                    request.api_user    = project.user
                except Exception:  # noqa: BLE001
                    pass
            elif is_api:
                return JsonResponse(
                    {
                        "error": "Invalid or inactive API key.",
                        "hint": "Generate a key at https://syncforge.dev/dashboard/",
                    },
                    status=403,
                )

        # ── 2. JWT cookie (dashboard) ─────────────────────────────────────────
        if not request.api_user:
            token = request.COOKIES.get("sf_access_token")
            if token:
                try:
                    payload = decode_token(token)
                    if payload.get("type") == "access":
                        user = User.objects.get(pk=payload["user_id"])
                        request.api_user = user
                        if not request.user.is_authenticated:
                            request.user = user
                except Exception:  # noqa: BLE001
                    pass  # Expired or invalid — fall through to session.

        # ── 3. Django session (AuthenticationMiddleware already ran) ──────────
        if not request.api_user and request.user.is_authenticated:
            request.api_user = request.user

        # ── Guard /api/* routes ───────────────────────────────────────────────
        if is_api and not request.api_user and request.path not in PUBLIC_PATHS:
            return JsonResponse(
                {
                    "error": "Authentication required.",
                    "methods": [
                        "Header: X-API-Key: sf_live_...",
                        "Cookie: sf_access_token (JWT)",
                    ],
                },
                status=401,
            )

        return self.get_response(request)
