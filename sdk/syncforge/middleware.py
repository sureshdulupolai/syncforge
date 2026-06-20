"""
SyncForge — Security Middleware
=================================

``SyncForgeSecurityMiddleware`` provides two features:

1. **Basic WAF (Web Application Firewall)**: Inspects the request path, query
   string, and POST body for well-known injection patterns (SQLi, XSS, path
   traversal). This is a defence-in-depth measure — it does **not** replace
   Django's built-in protections (CSRF, ORM parameterisation, template
   auto-escaping).

2. **Security response headers**: Injects HTTP security headers on every
   response to protect end users against common browser-based attacks.

Limitations
-----------
* The WAF uses regex pattern matching on the raw request text. Sophisticated
  obfuscated payloads may evade detection.
* The WAF does not substitute for proper input validation and parameterised
  queries in application code.
* This middleware should be placed **early** in the MIDDLEWARE list (after
  Django's SecurityMiddleware) so malicious requests are rejected before
  reaching application code.

Installation::

    # settings.py
    MIDDLEWARE = [
        'django.middleware.security.SecurityMiddleware',
        'syncforge.middleware.SyncForgeSecurityMiddleware',  # ← add here
        ...
    ]

Configuration (optional, in settings.py)::

    SYNCFORGE_WAF_ENABLED      = True      # Default: True
    SYNCFORGE_WAF_MAX_BODY     = 8192      # Max body bytes scanned (default: 8192)
    SYNCFORGE_WAF_BYPASS_PATHS = ['/health/']  # Paths that skip WAF scanning
"""
from __future__ import annotations

import logging
import re
import time
from typing import FrozenSet, List, Pattern

try:
    from django.http import HttpResponseForbidden, HttpResponse
    from django.utils.deprecation import MiddlewareMixin
    from django.conf import settings as _django_settings
    HAS_DJANGO = True
except ImportError:
    HAS_DJANGO = False
    class MiddlewareMixin:  # type: ignore[no-redef]
        pass

logger = logging.getLogger("syncforge.security")


# ── Compiled WAF Patterns ──────────────────────────────────────────────────────
# Each pattern targets a specific attack class. Regex is used (not string
# matching) to handle whitespace variants and case normalisations.
# re.IGNORECASE is applied at match time.

_WAF_PATTERNS: List[Pattern] = [
    # Path traversal
    re.compile(r'\.\.[/\\]'),

    # XSS — script injection
    re.compile(r'<script[\s\S]*?>'),
    re.compile(r'javascript\s*:'),
    re.compile(r'on(?:load|error|click|mouseover|focus|blur)\s*='),

    # SQL injection — UNION-based
    re.compile(r'\bUNION\s+(?:ALL\s+)?SELECT\b'),

    # SQL injection — tautologies
    re.compile(r"\bOR\s+['\"a-z0-9]+\s*=\s*['\"a-z0-9]+"),

    # SQL injection — destructive statements
    re.compile(r';\s*(?:DROP|TRUNCATE|DELETE\s+FROM|ALTER)\s+TABLE'),

    # SQL injection — comment injection
    re.compile(r'--\s+'),
    re.compile(r'/\*.*?\*/'),

    # Command injection
    re.compile(r'(?:;|\|)\s*(?:ls|cat|rm|wget|curl|bash|sh|python|perl)\b'),
]

# Compile a single combined pattern for slightly faster scanning.
_COMBINED_PATTERN: Pattern = re.compile(
    "|".join(p.pattern for p in _WAF_PATTERNS),
    re.IGNORECASE | re.DOTALL,
)


class SyncForgeSecurityMiddleware(MiddlewareMixin):
    """
    Drop-in security middleware for Django applications.

    Provides a basic WAF layer and enforces security response headers.
    All WAF checks are configurable via Django settings.
    """

    def process_request(self, request):
        if not HAS_DJANGO:
            return None

        request._sf_start_time = time.perf_counter()

        # ── Check bypass list ─────────────────────────────────────────────────
        bypass_paths: List[str] = getattr(
            _django_settings, "SYNCFORGE_WAF_BYPASS_PATHS", []
        )
        for bp in bypass_paths:
            if request.path.startswith(bp):
                return None

        # ── WAF check ─────────────────────────────────────────────────────────
        waf_enabled: bool = getattr(_django_settings, "SYNCFORGE_WAF_ENABLED", True)
        if not waf_enabled:
            return None

        if self._is_malicious(request):
            client_ip = (
                request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                or request.META.get("REMOTE_ADDR", "unknown")
            )
            logger.warning(
                "[SyncForge WAF] Blocked suspicious request from %s — %s %s",
                client_ip, request.method, request.path,
            )
            # Return 400 Bad Request (not 403) to avoid confirming the block to attackers.
            from django.http import HttpResponse
            return HttpResponse(
                "Bad Request",
                status=400,
                content_type="text/plain",
            )

        return None

    def process_response(self, request, response):
        if not HAS_DJANGO:
            return response

        # ── Request duration logging ──────────────────────────────────────────
        if hasattr(request, "_sf_start_time"):
            duration_ms = (time.perf_counter() - request._sf_start_time) * 1000
            status = response.status_code
            log_fn = (
                logger.error   if status >= 500 else
                logger.warning if status >= 400 else
                logger.info
            )
            log_fn(
                "[SyncForge] [%s] %s — %d (%.1fms)",
                request.method, request.path, status, duration_ms,
            )

        # ── Security headers ──────────────────────────────────────────────────
        # Prevent browsers from MIME-sniffing a response away from the declared type.
        response["X-Content-Type-Options"] = "nosniff"

        # Legacy XSS filter — still respected by older browsers.
        response["X-XSS-Protection"] = "1; mode=block"

        # Prevent clickjacking — deny framing from other origins.
        if "X-Frame-Options" not in response:
            response["X-Frame-Options"] = "SAMEORIGIN"

        # Enforce HTTPS for 1 year (only meaningful over HTTPS).
        if request.is_secure():
            response["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        # Restrict referrer information on cross-origin requests.
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Disable access to sensitive browser features not needed by a data
        # synchronisation platform.
        response["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), "
            "payment=(), usb=(), magnetometer=()"
        )

        # Content Security Policy — restrict resource loading.
        # Adjust 'script-src' if you load JS from CDNs.
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none';"
            )

        # NOTE: X-Powered-By is intentionally NOT set.
        # Advertising the technology stack aids fingerprinting by attackers.

        return response

    # ── Private ───────────────────────────────────────────────────────────────

    def _is_malicious(self, request) -> bool:
        """
        Scan the request URL and body for known malicious patterns.

        Scans (in order, short-circuiting on first match):

        1. Request path (URL-decoded by Django before this middleware runs).
        2. Query string (raw — not URL-decoded to avoid double-decode attacks).
        3. Request body (up to ``SYNCFORGE_WAF_MAX_BODY`` bytes, POST/PUT/PATCH only).

        Returns:
            ``True`` if a pattern matched (suspicious request).
            ``False`` if the request appears clean.
        """
        max_body: int = getattr(_django_settings, "SYNCFORGE_WAF_MAX_BODY", 8192)

        # Build the haystack from all scannable sources.
        parts: List[str] = [
            request.path,
            request.META.get("QUERY_STRING", ""),
        ]

        if request.method in ("POST", "PUT", "PATCH"):
            try:
                raw_body = request.body[:max_body].decode("utf-8", errors="ignore")
                parts.append(raw_body)
            except Exception:  # noqa: BLE001
                pass

        haystack = " ".join(parts)

        return bool(_COMBINED_PATTERN.search(haystack))
