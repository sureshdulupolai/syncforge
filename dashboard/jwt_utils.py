"""
JWT Utility — Token generation, validation & refresh.
Access token:  15 minutes
Refresh token: 7 days
"""
import jwt
import datetime
from django.conf import settings


def _now():
    return datetime.datetime.utcnow()


def generate_access_token(user):
    payload = {
        'user_id':   user.id,
        'username':  user.username,
        'is_staff':  user.is_staff,
        'is_super':  user.is_superuser,
        'exp':       _now() + datetime.timedelta(
                         minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        'iat':       _now(),
        'type':      'access',
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY,
                      algorithm=settings.JWT_ALGORITHM)


def generate_refresh_token(user):
    payload = {
        'user_id': user.id,
        'exp':     _now() + datetime.timedelta(
                       days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        'iat':     _now(),
        'type':    'refresh',
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY,
                      algorithm=settings.JWT_ALGORITHM)


def decode_token(token):
    """Returns payload dict or raises jwt.ExpiredSignatureError / jwt.InvalidTokenError."""
    return jwt.decode(token, settings.JWT_SECRET_KEY,
                      algorithms=[settings.JWT_ALGORITHM])


def is_token_expiring_soon(payload, threshold_minutes=3):
    """True if token expires within `threshold_minutes`."""
    exp = datetime.datetime.utcfromtimestamp(payload['exp'])
    remaining = (exp - _now()).total_seconds() / 60
    return remaining <= threshold_minutes
