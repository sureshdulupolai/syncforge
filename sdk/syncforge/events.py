"""
SyncForge — Unified Event System
==================================

A framework-agnostic telemetry and event tracking module.
All framework adapters (Django, Flask, FastAPI, SQLAlchemy, REST) 
must map into these standard internal events to ensure consistent observability.
"""

from enum import Enum
import logging

logger = logging.getLogger("syncforge.events")

class SyncForgeEvent(str, Enum):
    REQUEST_START = "request_start"
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    REFRESH_TRIGGER = "refresh_trigger"
    INVALIDATE_EVENT = "invalidate_event"
    CACHE_STORE_SUCCESS = "cache_store_success"
    CACHE_STORE_FAILURE = "cache_store_failure"
    STAMPEDE_LOCK_ACQUIRED = "stampede_lock_acquired"
    ASYNC_COALESCING_TRIGGERED = "async_coalescing_triggered"

class EventDispatcher:
    """
    A lightweight internal event dispatcher that telemetry/observability modules 
    can hook into without creating hard framework dependencies.
    """
    _listeners = {}

    @classmethod
    def subscribe(cls, event_type: SyncForgeEvent, callback: callable):
        if event_type not in cls._listeners:
            cls._listeners[event_type] = []
        cls._listeners[event_type].append(callback)

    @classmethod
    def dispatch(cls, event_type: SyncForgeEvent, payload: dict = None):
        payload = payload or {}
        # Avoid performance penalty if no listeners are attached
        if event_type not in cls._listeners:
            return
            
        for callback in cls._listeners[event_type]:
            try:
                callback(event_type, payload)
            except Exception as e:
                logger.debug(f"[SyncForge] Event listener failed for {event_type}: {e}")

def emit_event(event_type: SyncForgeEvent, **kwargs):
    """Utility function to fire an event."""
    EventDispatcher.dispatch(event_type, kwargs)
