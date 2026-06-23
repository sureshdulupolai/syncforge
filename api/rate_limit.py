"""
SyncForge — API Rate Limiter
=============================

Provides a sliding-window rate-limiting decorator for Django views.

The limiter uses Django's cache backend as its counter store. It tracks
request counts per project per one-minute window. When the limit is exceeded,
it returns HTTP 429 with a ``Retry-After`` header.

Because the counter is stored in the shared cache (Redis in production),
the limit is enforced consistently across all Gunicorn workers and servers.

Usage::

    from api.rate_limit import rate_limit

    @csrf_exempt
    @rate_limit(requests_per_minute=60)
    def my_view(request, table_name):
        ...
"""
from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable

from django.http import JsonResponse

logger = logging.getLogger("syncforge.rate_limit")


def rate_limit(requests_per_minute: int = 60) -> Callable:
    """
    Sliding-window rate-limiting decorator for Django view functions.

    Limits are applied **per project** (identified from ``request.api_project``).
    Unauthenticated requests pass through without rate limiting (authentication
    middleware handles those separately).

    The sliding window is approximated using two one-minute buckets: the
    current minute and the previous minute. This avoids the "boundary spike"
    problem of a pure fixed-window approach while remaining lightweight.

    Args:
        requests_per_minute:
            Maximum number of requests allowed within any 60-second window.
            Default: ``60``.

    Returns:
        A decorator that wraps a Django view function.

    HTTP Responses:
        429 Too Many Requests — when the limit is exceeded, with headers::

            Retry-After: 60
            X-RateLimit-Limit: <requests_per_minute>
            X-RateLimit-Remaining: 0

    Example::

        @csrf_exempt
        @rate_limit(requests_per_minute=120)
        def high_volume_endpoint(request):
            ...
    """
    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            project = getattr(request, "api_project", None)
            
            # Determine rate limit key
            if project:
                identifier = f"project_{project.id}"
            else:
                # Fallback to IP address for public/auth routes (like login/register)
                client_ip = (
                    request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                    or request.META.get("REMOTE_ADDR", "unknown")
                )
                identifier = f"ip_{client_ip}"

            try:
                from django.core.cache import cache

                now = int(time.time())
                current_window = now // 60
                previous_window = current_window - 1

                current_key  = f"sf_rl_{identifier}_{current_window}"
                previous_key = f"sf_rl_{identifier}_{previous_window}"

                # Sliding window: weight the previous window by the fraction of
                # the current window already elapsed, then add current window count.
                current_count  = cache.get(current_key, 0)
                previous_count = cache.get(previous_key, 0)
                elapsed_fraction = (now % 60) / 60.0
                estimated_count = int(previous_count * (1.0 - elapsed_fraction)) + current_count

                remaining = max(0, requests_per_minute - estimated_count)

                if estimated_count >= requests_per_minute:
                    logger.warning(
                        "[SyncForge] Rate limit exceeded for %s "
                        "(estimated %d/%d requests in window).",
                        identifier, estimated_count, requests_per_minute,
                    )
                    if request.path.startswith('/api/'):
                        response = JsonResponse(
                            {
                                "error": "Rate limit exceeded.",
                                "detail": f"Maximum {requests_per_minute} requests per minute.",
                                "retry_after": 60,
                            },
                            status=429,
                        )
                    else:
                        from django.shortcuts import render
                        response = render(request, '429.html', status=429)

                    response["Retry-After"]           = "60"
                    response["X-RateLimit-Limit"]     = str(requests_per_minute)
                    response["X-RateLimit-Remaining"] = "0"
                    return response

                # Increment current window counter (TTL = 2 minutes for safety).
                cache.set(current_key, current_count + 1, timeout=120)

                response = view_func(request, *args, **kwargs)

                # Attach rate-limit info headers to the response.
                response["X-RateLimit-Limit"]     = str(requests_per_minute)
                response["X-RateLimit-Remaining"] = str(remaining - 1)
                return response

            except Exception as exc:  # noqa: BLE001
                # If the cache is unavailable, do not block the request.
                # Log and fall through — degraded rate limiting is better than
                # a complete outage.
                logger.warning(
                    "[SyncForge] Rate limiter error (falling through): %s", exc
                )
                return view_func(request, *args, **kwargs)

        return wrapper
    return decorator
