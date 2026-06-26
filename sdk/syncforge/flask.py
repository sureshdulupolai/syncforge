"""
SyncForge — Flask Integration
=============================

Provides an extension pattern for Flask to register the SyncForge client
and handle WAF rate limiting.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("syncforge.flask")

try:
    from flask import request, jsonify
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

class SyncForgeFlask:
    """
    Flask extension for SyncForge.
    
    Initialises the WAF middleware and attaches the SyncForge client to the app.

    Example::
    
        from flask import Flask
        from syncforge.flask import SyncForgeFlask
        from sf_client import sf
        
        app = Flask(__name__)
        sf_ext = SyncForgeFlask(app, sf)
    """
    def __init__(self, app=None, sf_client=None):
        self.sf_client = sf_client
        if app is not None and sf_client is not None:
            self.init_app(app, sf_client)

    def init_app(self, app, sf_client):
        if not HAS_FLASK:
            raise ImportError("Flask is not installed.")
            
        self.sf_client = sf_client
        app.extensions['syncforge'] = self

        @app.before_request
        def check_waf():
            if hasattr(self.sf_client, 'preload_cache'):
                self.sf_client.preload_cache()
                
            from syncforge.exceptions import SyncForgeWAFError
            
            ip = self._get_client_ip()
            if hasattr(self.sf_client, "_local"):
                self.sf_client._local.client_ip = ip
                
        @app.before_request
        def check_maintenance():
            import time
            from .engine import Maintenance
            
            if time.time() > Maintenance.next_cleanup:
                if Maintenance.lock.acquire(blocking=False):
                    try:
                        Maintenance.next_cleanup = Maintenance.compute_next()
                        import threading
                        def _cleanup():
                            try:
                                if self.sf_client:
                                    self.sf_client.clear_syncforge_cache()
                            except Exception as e:
                                logger.error(f"[SyncForge Maintenance] Cleanup failed: {e}")
                        threading.Thread(target=_cleanup, daemon=True).start()
                    finally:
                        Maintenance.lock.release()
                
        @app.after_request
        def cleanup_waf(response):
            if hasattr(self.sf_client, "_local"):
                self.sf_client._local.client_ip = None
            return response
            
        @app.errorhandler(Exception)
        def handle_syncforge_error(e):
            from syncforge.exceptions import SyncForgeWAFError
            if isinstance(e, SyncForgeWAFError):
                return jsonify({
                    "error": "Rate limit exceeded. Too many requests.",
                    "blocked_for_seconds": e.block_time
                }), 429
            # Allow other error handlers to process
            raise e

    def _get_client_ip(self) -> str:
        x_forwarded_for = request.headers.get('X-Forwarded-For')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.remote_addr or ""
