"""
SyncForge — FastAPI Integration
===============================

Provides middleware and dependency injection for FastAPI.
"""
from __future__ import annotations

import logging
from typing import Callable, Any

logger = logging.getLogger("syncforge.fastapi")

try:
    from fastapi import Request, Response
    from fastapi.responses import JSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    BaseHTTPMiddleware = object  # type: ignore

class SyncForgePreloadMiddleware(BaseHTTPMiddleware):
    """
    Middleware to automatically preload the SyncForge cache from Disk to RAM 
    on the first request to the FastAPI application.
    """
    def __init__(self, app, sf_client):
        super().__init__(app)
        self.sf_client = sf_client

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self.sf_client and hasattr(self.sf_client, 'preload_cache'):
            self.sf_client.preload_cache()
        return await call_next(request)

class SyncForgeWAFMiddleware(BaseHTTPMiddleware):
    """
    SyncForge Anti-DDoS Web Application Firewall (WAF) Middleware for FastAPI.

    Extracts the client IP from the incoming request and checks the rate limit.
    If the user exceeds the rate limit defined in `@sync_model(waf_enabled=True)`, 
    this middleware returns a 429 Too Many Requests response.
    """
    def __init__(self, app, sf_client):
        super().__init__(app)
        self.sf_client = sf_client

    def _get_client_ip(self, request: Request) -> str:
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        if request.client:
            return request.client.host
        return ""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        from syncforge.exceptions import SyncForgeWAFError

        ip = self._get_client_ip(request)
        
        # Inject IP into Thread-local storage for SyncForge client
        if hasattr(self.sf_client, "_local"):
            self.sf_client._local.client_ip = ip

        try:
            response = await call_next(request)
            return response
        except SyncForgeWAFError as e:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded. Too many requests.",
                    "blocked_for_seconds": e.block_time
                }
            )
        finally:
            if hasattr(self.sf_client, "_local"):
                self.sf_client._local.client_ip = None

def get_syncforge(sf_client) -> Callable:
    """
    FastAPI dependency injection provider.
    
    Example:
        from syncforge.fastapi import get_syncforge
        from sf_client import sf

        @app.get("/items")
        def read_items(sf=Depends(get_syncforge(sf))):
            return sf.cache_query(...)
    """
    def _dependency():
        return sf_client
    return _dependency
