"""
SyncForge — Generic Python Decorators
=====================================

Provides the ``@sync_function`` decorator for framework-agnostic usage.
Can be used with plain Python scripts, Flask, FastAPI, or any custom backend.
"""
import asyncio
import logging
from functools import wraps
from typing import Callable, Any, Optional

logger = logging.getLogger("syncforge.decorators")

def sync_function(
    sf_client,
    table_name: str,
    sync_mode: str = "event",
    active: bool = True,
    storage_mode: str = "ram_disk",
    compression: str = "none",
    encryption: bool = True,
    priority: str = "medium",
    refresh_interval: int = 0,
    timeout: Optional[int] = 3600,
    waf_enabled: bool = False,
    max_requests: int = 100,
    block_time_sec: int = 86400,
):
    """
    Decorator for generic Python functions to invalidate cache entries when called.
    Supports both synchronous and asynchronous (FastAPI/Starlette) functions.
    
    Example:
        @sync_function(sf, table_name="users")
        def update_user(user_id, data):
            db.execute("UPDATE users SET ...")
    """
    def decorator(func: Callable) -> Callable:
        # Register the table explicitly at decorator application time
        sf_client.core.register_model(
            table_name=table_name,
            sync_mode=sync_mode,
            active=active,
            storage_mode=storage_mode,
            compression=compression,
            encryption=encryption,
            priority=priority,
            refresh_interval=refresh_interval,
            timeout=timeout,
            waf_enabled=waf_enabled,
            max_requests=max_requests,
            block_time_sec=block_time_sec,
            local_metadata_updater=None,  # No native ORM tracking
            local_metadata_fetcher=None,
        )

        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs) -> Any:
                result = await func(*args, **kwargs)
                sf_client.core.trigger_sync(table_name)
                return result
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs) -> Any:
                result = func(*args, **kwargs)
                sf_client.core.trigger_sync(table_name)
                return result
            return sync_wrapper

    return decorator
