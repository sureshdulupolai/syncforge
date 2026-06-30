"""
SyncForge — Distributed Store Abstraction
=========================================

Handles the framework-agnostic persistence layer for WAF rate-limiting,
registry tracking, and stampede coordination.

STRICT RULE: The backend is selected ONCE at initialization. No auto-detection
of external services (Redis) is allowed. It must be explicitly configured.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, List, Optional, Set

from .events import SyncForgeEvent, emit_event

logger = logging.getLogger("syncforge.store")

class BaseStore:
    def get(self, key: str, default: Any = None) -> Any:
        raise NotImplementedError

    def set(self, key: str, value: Any, timeout: Optional[int] = None) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def delete_many(self, keys: List[str]) -> None:
        for key in keys:
            self.delete(key)

    def clear_syncforge_cache(self) -> None:
        pass

class InMemoryStore(BaseStore):
    """Default fallback store. Always safe, always available."""
    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key not in self._data:
                return default
            val, exp = self._data[key]
            if exp and time.time() > exp:
                del self._data[key]
                return default
            return val

    def set(self, key: str, value: Any, timeout: Optional[int] = None) -> None:
        with self._lock:
            exp = time.time() + timeout if timeout else 0
            self._data[key] = (value, exp)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear_syncforge_cache(self) -> None:
        with self._lock:
            keys_to_delete = [k for k in self._data.keys() if k.startswith("syncforge_")]
            for k in keys_to_delete:
                del self._data[k]

class DjangoCacheStore(BaseStore):
    """Optional store for Django users."""
    def __init__(self):
        try:
            from django.core.cache import cache
            self._cache = cache
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Django cache: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    def set(self, key: str, value: Any, timeout: Optional[int] = None) -> None:
        self._cache.set(key, value, timeout)

    def delete(self, key: str) -> None:
        self._cache.delete(key)

    def delete_many(self, keys: List[str]) -> None:
        self._cache.delete_many(keys)

    def clear_syncforge_cache(self) -> None:
        try:
            if hasattr(self._cache, 'delete_pattern'):
                self._cache.delete_pattern("syncforge_*")
            elif hasattr(self._cache, 'keys'):
                keys = self._cache.keys("syncforge_*")
                if keys:
                    self._cache.delete_many(keys)
            elif hasattr(self._cache, '_cache'):
                # Locmem fallback
                keys = [k for k in self._cache._cache.keys() if str(k).split(":")[-1].startswith("syncforge_")]
                if keys:
                    self._cache.delete_many([str(k).split(":")[-1] for k in keys])
            else:
                logger.warning("Django cache backend does not support wildcard deletion. Cannot selectively clear SyncForge cache.")
        except Exception as e:
            logger.error(f"[SyncForge] Failed to clear Django cache: {e}")

class RedisStore(BaseStore):
    """Optional store for explicit Redis configurations."""
    def __init__(self, redis_url: str):
        try:
            import redis
            # Must be explicitly configured. Connection is lazy but validated.
            self._redis = redis.from_url(redis_url, decode_responses=False)
            self._redis.ping()
        except ImportError:
            raise RuntimeError("redis library is not installed.")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Redis: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        import pickle
        val = self._redis.get(key)
        if val is None:
            return default
        try:
            return pickle.loads(val)
        except Exception:
            return default

    def set(self, key: str, value: Any, timeout: Optional[int] = None) -> None:
        import pickle
        try:
            raw = pickle.dumps(value)
            if timeout:
                self._redis.setex(key, timeout, raw)
            else:
                self._redis.set(key, raw)
        except Exception as e:
            logger.error(f"[SyncForge Redis] Set failed for {key}: {e}")

    def delete(self, key: str) -> None:
        self._redis.delete(key)

    def delete_many(self, keys: List[str]) -> None:
        if keys:
            self._redis.delete(*keys)

    def clear_syncforge_cache(self) -> None:
        try:
            cursor = '0'
            while cursor != 0:
                cursor, keys = self._redis.scan(cursor=cursor, match="syncforge_*", count=100)
                if keys:
                    self._redis.delete(*keys)
        except Exception as e:
            logger.error(f"[SyncForge Redis] Clear failed: {e}")

class SQLiteStore(BaseStore):
    """
    Local persistence backend using SQLite.
    Survives server restarts and hot-reloads during local development.
    """
    def __init__(self, db_path: str = ".syncforge_cache.db"):
        import sqlite3
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        import sqlite3
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS sf_cache
                         (key TEXT PRIMARY KEY, value BLOB, expire_time REAL)''')
            conn.commit()
            conn.close()

    def get(self, key: str, default: Any = None) -> Any:
        import sqlite3
        import pickle
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                c = conn.cursor()
                c.execute("SELECT value, expire_time FROM sf_cache WHERE key = ?", (key,))
                row = c.fetchone()
                conn.close()
                if row:
                    val_bytes, exp = row
                    if exp and time.time() > exp:
                        self.delete(key)
                        return default
                    return pickle.loads(val_bytes)
            except Exception:
                pass
            return default

    def set(self, key: str, value: Any, timeout: Optional[int] = None) -> None:
        import sqlite3
        import pickle
        with self._lock:
            try:
                exp = time.time() + timeout if timeout else 0
                val_bytes = pickle.dumps(value)
                conn = sqlite3.connect(self.db_path)
                c = conn.cursor()
                c.execute("REPLACE INTO sf_cache (key, value, expire_time) VALUES (?, ?, ?)",
                          (key, val_bytes, exp))
                conn.commit()
                conn.close()
            except Exception:
                pass

    def delete(self, key: str) -> None:
        import sqlite3
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                c = conn.cursor()
                c.execute("DELETE FROM sf_cache WHERE key = ?", (key,))
                conn.commit()
                conn.close()
            except Exception:
                pass

    def clear_syncforge_cache(self) -> None:
        import sqlite3
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                c = conn.cursor()
                c.execute("DELETE FROM sf_cache")
                conn.commit()
                conn.close()
            except Exception:
                pass

class StoreManager:
    """
    Manages the static backend selection and fallback logic.
    Backend must be selected ONCE at startup.
    """
    def __init__(self, backend_type: str = "memory", redis_url: str = None):
        self._store = None
        self._fallback_store = InMemoryStore()
        
        try:
            if backend_type == "redis":
                if not redis_url:
                    raise ValueError("redis_url is required when backend_type='redis'")
                self._store = RedisStore(redis_url)
                logger.info("[SyncForge] Connected to Redis Backend successfully.")
            elif backend_type == "django":
                self._store = DjangoCacheStore()
                logger.info("[SyncForge] Hooked into Django Cache Backend.")
            elif backend_type == "sqlite":
                self._store = SQLiteStore()
                logger.info("[SyncForge] Connected to SQLite Backend successfully.")
            else:
                self._store = self._fallback_store
                logger.info("[SyncForge] Using default InMemoryStore.")
        except Exception as e:
            # FAILURE SAFETY RULE: Fallback gracefully, never crash
            logger.error(f"[SyncForge] Failed to initialize backend '{backend_type}': {e}. Falling back to InMemoryStore.")
            self._store = self._fallback_store
            emit_event(SyncForgeEvent.CACHE_STORE_FAILURE, backend=backend_type, error=str(e))

    @property
    def store(self) -> BaseStore:
        return self._store

    def clear_syncforge_cache(self) -> None:
        """Clears all syncforge_* prefixed keys from the active backend."""
        self._store.clear_syncforge_cache()
        if self._store is not self._fallback_store:
            self._fallback_store.clear_syncforge_cache()

    # Helper methods for registry management (used by client.py)
    def register_cache_key(self, table_name: str, cache_key: str, timeout: Optional[int]) -> None:
        registry_key = f"syncforge_registry_{table_name}"
        registry_ttl = timeout if timeout is not None else 86400
        try:
            existing_keys: set = self._store.get(registry_key) or set()
            existing_keys.add(cache_key)
            self._store.set(registry_key, existing_keys, registry_ttl)
            emit_event(SyncForgeEvent.CACHE_STORE_SUCCESS, op="register_key", table=table_name)
        except Exception as e:
            logger.warning(f"[SyncForge] Failed to register cache key. Fallback triggered: {e}")
            # Fallback to local memory registry on failure
            existing_keys: set = self._fallback_store.get(registry_key) or set()
            existing_keys.add(cache_key)
            self._fallback_store.set(registry_key, existing_keys, registry_ttl)

    def invalidate_table_registry(self, table_name: str) -> None:
        registry_key = f"syncforge_registry_{table_name}"
        try:
            keys: set = self._store.get(registry_key) or set()
            if keys:
                self._store.delete_many(list(keys))
                self._store.delete(registry_key)
                emit_event(SyncForgeEvent.INVALIDATE_EVENT, table=table_name, count=len(keys))
            # Also clear fallback just in case
            fb_keys: set = self._fallback_store.get(registry_key) or set()
            if fb_keys:
                self._fallback_store.delete_many(list(fb_keys))
                self._fallback_store.delete(registry_key)
        except Exception as e:
            logger.warning(f"[SyncForge] Cache registry invalidation failed: {e}")

    def get_waf_hits(self, waf_key: str) -> Optional[int]:
        try:
            return self._store.get(waf_key)
        except Exception:
            return self._fallback_store.get(waf_key)
            
    def set_waf_hits(self, waf_key: str, hits: int, timeout: int) -> None:
        try:
            self._store.set(waf_key, hits, timeout)
        except Exception:
            self._fallback_store.set(waf_key, hits, timeout)
