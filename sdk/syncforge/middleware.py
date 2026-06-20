"""
SyncForge Security Middleware
Professional-grade request/response logging and basic WAF protection for Django apps.
"""
import time
import logging

try:
    from django.http import HttpResponseForbidden
    from django.utils.deprecation import MiddlewareMixin
    HAS_DJANGO = True
except ImportError:
    HAS_DJANGO = False
    class MiddlewareMixin:
        pass

logger = logging.getLogger('syncforge.security')

class SyncForgeSecurityMiddleware(MiddlewareMixin):
    """
    Drop-in security and logging middleware.
    Add 'syncforge.middleware.SyncForgeSecurityMiddleware' to your MIDDLEWARE setting.
    """
    
    # Common malicious patterns to block automatically
    MALICIOUS_PATTERNS = [
        '../',              # Path Traversal
        '<script',          # XSS
        'javascript:',      # XSS
        'UNION SELECT',     # SQLi
        'OR 1=1',           # SQLi
        '-- ',              # SQL comment injection
    ]

    def process_request(self, request):
        if not HAS_DJANGO:
            return None
            
        request._syncforge_start_time = time.time()
        
        # Basic WAF (Web Application Firewall) checks
        if self._is_malicious(request):
            ip = request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR')
            logger.warning(f"[SyncForge Security] Blocked malicious request from {ip} on {request.path}")
            return HttpResponseForbidden("Blocked by SyncForge Security Firewall.")
            
        return None

    def process_response(self, request, response):
        if not HAS_DJANGO:
            return response
            
        # Logging
        if hasattr(request, '_syncforge_start_time'):
            duration = (time.time() - request._syncforge_start_time) * 1000
            method = request.method
            path = request.path
            status = response.status_code
            
            # Format: [POST] /api/users/ - 200 OK (45.2ms)
            if status >= 500:
                level = logger.error
            elif status >= 400:
                level = logger.warning
            else:
                level = logger.info
                
            level(f"[SyncForge] [{method}] {path} - {status} ({duration:.1f}ms)")
            
        # Inject Security Headers
        response['X-Powered-By'] = 'SyncForge'
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-XSS-Protection'] = '1; mode=block'
        
        return response

    def _is_malicious(self, request):
        path = request.path.upper()
        query = request.META.get('QUERY_STRING', '').upper()
        
        for pattern in self.MALICIOUS_PATTERNS:
            p = pattern.upper()
            if p in path or p in query:
                return True
                
        return False
