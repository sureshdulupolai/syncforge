"""
Unified API + Auth Middleware
─────────────────────────────
Supports three auth methods on all requests:
  1. X-API-Key header  → REST API clients (curl, SDKs, external apps)
  2. JWT cookie        → SyncForge web dashboard (browser)
  3. Django session    → SyncForge web dashboard (fallback)

On /api/* routes: X-API-Key is required unless JWT/session user is authenticated.
On /dashboard/* routes: JWT or session required.

Attaches to request:
  request.api_project  — Project object (set when X-API-Key used)
  request.api_user     — User object (always set when auth succeeds)
"""
import jwt
from django.http import JsonResponse
from django.contrib.auth.models import User
from dashboard.models import APIKey
from dashboard.jwt_utils import decode_token


# Paths that never need auth
PUBLIC_PATHS = ('/', '/dashboard/login/', '/dashboard/register/',
                '/dashboard/sf-admin-init-9x7k/register/',
                '/dashboard/auth/token/refresh/')


class UnifiedAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.api_project = None
        request.api_user    = None

        is_api = request.path.startswith('/api/')

        # ── 1. Try X-API-Key (REST clients) ──────────────────────────────────
        api_key_header = request.headers.get('X-API-Key') or request.GET.get('api_key')
        if api_key_header:
            try:
                key_obj = (APIKey.objects
                           .select_related('project', 'project__user')
                           .get(key=api_key_header, is_active=True))
                import django.utils.timezone as tz
                key_obj.last_used = tz.now()
                key_obj.save(update_fields=['last_used'])
                request.api_project = key_obj.project
                request.api_user    = key_obj.project.user
            except APIKey.DoesNotExist:
                if is_api:
                    return JsonResponse(
                        {'error': 'Invalid or inactive API key.',
                         'hint': 'Generate a key at https://syncforge.io/dashboard/'},
                        status=403)

        # ── 2. Try JWT cookie ────────────────────────────────────────────────
        if not request.api_user:
            token = request.COOKIES.get('sf_access_token')
            if token:
                try:
                    payload = decode_token(token)
                    if payload.get('type') == 'access':
                        user = User.objects.get(pk=payload['user_id'])
                        request.api_user = user
                        # also inject into Django's request.user if not already set
                        if not request.user.is_authenticated:
                            request.user = user
                except Exception:
                    pass

        # ── 3. Django session (already handled by AuthenticationMiddleware) ──
        if not request.api_user and request.user.is_authenticated:
            request.api_user = request.user

        # ── Guard /api/* routes ───────────────────────────────────────────────
        if is_api and not request.api_user:
            return JsonResponse(
                {'error': 'Authentication required.',
                 'methods': [
                     'Header: X-API-Key: sf_live_...',
                     'Cookie: sf_access_token (JWT)',
                 ]},
                status=401)

        response = self.get_response(request)
        return response
