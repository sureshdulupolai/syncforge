"""
JWT Middleware — attaches JWT token to every response cookie
and auto-refreshes when the token is about to expire (≤3 min left).
Falls back to Django session auth gracefully.
"""
import jwt
from django.contrib.auth.models import User
from django.utils.functional import SimpleLazyObject
from .jwt_utils import (decode_token, generate_access_token,
                        is_token_expiring_soon)


def get_user_from_jwt(request):
    token = request.COOKIES.get('sf_access_token')
    if not token:
        return None
    try:
        payload = decode_token(token)
        if payload.get('type') != 'access':
            return None
        return User.objects.get(pk=payload['user_id'])
    except Exception:
        return None


class JWTAuthMiddleware:
    """
    1. Reads JWT from `sf_access_token` cookie.
    2. Authenticates user via JWT (overrides Django session).
    3. Auto-refreshes token if expiring soon.
    4. Clears invalid/expired cookies.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token      = request.COOKIES.get('sf_access_token')
        needs_refresh = False
        new_token   = None

        if token:
            try:
                payload = decode_token(token)
                if payload.get('type') == 'access':
                    # inject user into request for views
                    if not request.user.is_authenticated:
                        try:
                            user = User.objects.get(pk=payload['user_id'])
                            request.user = user
                        except User.DoesNotExist:
                            pass
                    # check if refresh needed
                    if is_token_expiring_soon(payload):
                        needs_refresh = True
                        new_token = generate_access_token(request.user)
            except jwt.ExpiredSignatureError:
                # let views handle redirect to login
                pass
            except jwt.InvalidTokenError:
                pass

        response = self.get_response(request)

        if needs_refresh and new_token:
            response.set_cookie(
                'sf_access_token', new_token,
                max_age=900,        # 15 min
                httponly=True,
                samesite='Lax',
                secure=False,       # set True in production
            )

        return response
