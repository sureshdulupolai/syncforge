from functools import wraps
from django.core.cache import cache
from django.shortcuts import render

def rate_limit(key_prefix, max_requests=5, timeout=60):
    """
    Rate limiting decorator for Django views using LocMemCache.
    If the client IP exceeds `max_requests` within `timeout` seconds,
    it renders a professional 429 Too Many Requests template.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # Extract client IP safely
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip = x_forwarded_for.split(',')[0].strip()
            else:
                ip = request.META.get('REMOTE_ADDR')
            
            cache_key = f"rate_limit_{key_prefix}_{ip}"
            
            # Use atomic add if possible, but for LocMemCache get/set is fine
            requests_made = cache.get(cache_key, 0)
            
            if requests_made >= max_requests:
                return render(request, '429.html', status=429)
                
            cache.set(cache_key, requests_made + 1, timeout)
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator
