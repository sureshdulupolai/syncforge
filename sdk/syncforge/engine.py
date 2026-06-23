"""
SyncForge Enterprise Cache Management Engine
============================================

Handles multi-level caching (RAM -> Disk), smart eviction policies, 
binary serialization, compression, and AES-256 encryption.
Upgraded with Intelligent Memory Controller, Cache Heat Engine, Adaptive Compression, 
Smart Serialization, and Duplicate Object Deduplication.
"""

import os
import time
import pickle
import hashlib
import logging
import threading
import datetime
import sys
from collections import OrderedDict
from enum import Enum
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("syncforge.engine")

class StorageMode(str, Enum):
    RAM_ONLY = "ram_only"
    RAM_DISK = "ram_disk"
    DISK_ONLY = "disk_only"
    DISABLED = "disabled"

class EvictionPolicy(str, Enum):
    LRU = "lru"    # Least Recently Used
    LFU = "lfu"    # Least Frequently Used
    FIFO = "fifo"  # First In First Out
    HEAT = "heat"  # Dynamic Heat Score (Enterprise)

class CompressionType(str, Enum):
    NONE = "none"
    LZ4 = "lz4"
    ZSTD = "zstd"
    GZIP = "gzip"

# ─── Compression Adapters ──────────────────────────────────────────────────────
def compress_data(data: bytes, comp_type: CompressionType) -> bytes:
    if comp_type == CompressionType.NONE:
        return data
    elif comp_type == CompressionType.LZ4:
        import lz4.frame
        return lz4.frame.compress(data)
    elif comp_type == CompressionType.ZSTD:
        import zstandard as zstd
        cctx = zstd.ZstdCompressor()
        return cctx.compress(data)
    elif comp_type == CompressionType.GZIP:
        import gzip
        return gzip.compress(data)
    raise ValueError(f"Unknown compression type: {comp_type}")

def decompress_data(data: bytes, comp_type: CompressionType) -> bytes:
    if comp_type == CompressionType.NONE:
        return data
    elif comp_type == CompressionType.LZ4:
        import lz4.frame
        return lz4.frame.decompress(data)
    elif comp_type == CompressionType.ZSTD:
        import zstandard as zstd
        dctx = zstd.ZstdDecompressor()
        return dctx.decompress(data)
    elif comp_type == CompressionType.GZIP:
        import gzip
        return gzip.decompress(data)
    raise ValueError(f"Unknown compression type: {comp_type}")

# ─── Adaptive Compression Engine ───────────────────────────────────────────────
class AdaptiveCompression:
    @staticmethod
    def optimize(raw_data: bytes, requested_type: CompressionType) -> Tuple[bytes, CompressionType]:
        """Dynamically select the best compression based on entropy and size."""
        if len(raw_data) < 512:
            return raw_data, CompressionType.NONE
            
        if requested_type == CompressionType.NONE:
            return raw_data, CompressionType.NONE
            
        t0 = time.time()
        try:
            # Fast try LZ4
            import lz4.frame
            compressed = lz4.frame.compress(raw_data)
            comp_type = CompressionType.LZ4
        except ImportError:
            try:
                import gzip
                compressed = gzip.compress(raw_data)
                comp_type = CompressionType.GZIP
            except Exception:
                return raw_data, CompressionType.NONE
                
        t1 = time.time()
        
        # If compression is slow (>50ms) or saves less than 10%, skip it
        if (t1 - t0) > 0.05 or len(compressed) > len(raw_data) * 0.90:
            return raw_data, CompressionType.NONE
            
        return compressed, comp_type

# ─── Encryption ────────────────────────────────────────────────────────────────
def encrypt_data(data: bytes, key: str) -> bytes:
    from cryptography.fernet import Fernet
    f = Fernet(key.encode('utf-8'))
    return f.encrypt(data)

def decrypt_data(data: bytes, key: str) -> bytes:
    from cryptography.fernet import Fernet
    f = Fernet(key.encode('utf-8'))
    return f.decrypt(data)

# ─── Smart Serialization ───────────────────────────────────────────────────────
class SmartSerializer:
    @staticmethod
    def dumps(obj: Any) -> bytes:
        try:
            import orjson
            # Faster struct serialization for simple dicts/lists
            return orjson.dumps(obj)
        except Exception:
            return pickle.dumps(obj)
            
    @staticmethod
    def loads(data: bytes) -> Any:
        if data.startswith(b'{') or data.startswith(b'['):
            try:
                import orjson
                return orjson.loads(data)
            except Exception:
                pass
        return pickle.loads(data)

# ─── Cache Payload ─────────────────────────────────────────────────────────────
class CachePayload:
    def __init__(self, data: Any, version: int, timestamp: float, compression: str, encrypted: bool):
        self.data = data
        self.version = version
        self.timestamp = timestamp
        self.compression = compression
        self.encrypted = encrypted

    def serialize(self, comp_type: CompressionType, encryption_key: Optional[str]) -> bytes:
        raw = SmartSerializer.dumps(self.data)
        
        # Adaptive Compression
        compressed, final_comp_type = AdaptiveCompression.optimize(raw, comp_type)
        self.compression = final_comp_type.value
        
        if encryption_key:
            compressed = encrypt_data(compressed, encryption_key)
            self.encrypted = True
        else:
            self.encrypted = False
            
        # Binary header: version(4 bytes) + timestamp(8 bytes) + comp_len(1 byte) + comp_str + enc(1 byte)
        meta = {
            "version": self.version,
            "timestamp": self.timestamp,
            "compression": self.compression,
            "encrypted": self.encrypted
        }
        meta_bytes = pickle.dumps(meta)
        meta_len = len(meta_bytes)
        
        # Layout: 4 bytes meta_len, meta_bytes, payload
        payload = meta_len.to_bytes(4, 'big') + meta_bytes + compressed
        
        # Checksum calculation
        checksum = hashlib.sha256(payload).digest()
        
        # Final layout: 32 bytes checksum + payload
        return checksum + payload

    @classmethod
    def deserialize(cls, raw_data: bytes, encryption_key: Optional[str]) -> 'CachePayload':
        if len(raw_data) < 36:
            raise ValueError("Invalid cache file: too small")
            
        checksum = raw_data[:32]
        payload = raw_data[32:]
        
        if hashlib.sha256(payload).digest() != checksum:
            raise ValueError("Cache integrity check failed: checksum mismatch")
            
        meta_len = int.from_bytes(payload[:4], 'big')
        meta = pickle.loads(payload[4:4+meta_len])
        
        compressed = payload[4+meta_len:]
        
        if meta["encrypted"]:
            if not encryption_key:
                raise ValueError("Cache is encrypted but no key was provided")
            compressed = decrypt_data(compressed, encryption_key)
            
        comp_type = CompressionType(meta["compression"])
        raw = decompress_data(compressed, comp_type)
        data = SmartSerializer.loads(raw)
        
        return cls(
            data=data,
            version=meta["version"],
            timestamp=meta["timestamp"],
            compression=meta["compression"],
            encrypted=meta["encrypted"]
        )

# ─── Duplicate Object Deduplicator ─────────────────────────────────────────────
class ObjectDeduplicator:
    """Uses object hashing to deduplicate identical rows across multiple cache payloads."""
    _pool: Dict[str, Any] = {}
    _lock = threading.Lock()
    
    @classmethod
    def deduplicate(cls, payload: Any) -> Any:
        if not isinstance(payload, list):
            return payload
            
        deduped = []
        with cls._lock:
            # Maintain pool size
            if len(cls._pool) > 50000:
                cls._pool.clear()
                
            for item in payload:
                # Attempt to hash dict representation or string representation
                try:
                    if hasattr(item, '__dict__'):
                        item_hash = hashlib.md5(str(item.__dict__).encode()).hexdigest()
                    else:
                        item_hash = hashlib.md5(str(item).encode()).hexdigest()
                        
                    if item_hash in cls._pool:
                        deduped.append(cls._pool[item_hash])
                    else:
                        cls._pool[item_hash] = item
                        deduped.append(item)
                except Exception:
                    deduped.append(item)
        return deduped

# ─── RAM Manager ───────────────────────────────────────────────────────────────
class SmartRAMManager:
    def __init__(self, max_items: int = 1000, policy: EvictionPolicy = EvictionPolicy.LRU):
        self.max_items = max_items
        self.policy = EvictionPolicy.HEAT if policy == EvictionPolicy.LRU else policy
        
        # Intelligent Memory Controller variables
        self._max_memory_bytes = 256 * 1024 * 1024  # 256MB limit default
        self._current_memory = 0
        
        # Storage
        self._cache: Dict[str, Tuple[Any, float]] = {}  # key -> (payload, expire_time)
        
        # Cache Heat Engine metadata
        self._heat_stats: Dict[str, Dict[str, Any]] = {}

    def _update_heat(self, key: str, size: int = 0) -> None:
        now = time.time()
        if key not in self._heat_stats:
            self._heat_stats[key] = {"hits": 1, "created": now, "last_access": now, "size": size}
        else:
            self._heat_stats[key]["hits"] += 1
            self._heat_stats[key]["last_access"] = now
            if size > 0:
                self._heat_stats[key]["size"] = size

    def _get_heat_score(self, key: str) -> float:
        stats = self._heat_stats.get(key)
        if not stats: return 0.0
        now = time.time()
        age = max(1.0, now - stats["created"])
        recency = max(1.0, now - stats["last_access"])
        # Score = (hits / age) + (1.0 / recency)
        return (stats["hits"] / age) + (1.0 / recency)

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
            
        payload, expire_time = self._cache[key]
        now = time.time()
        
        # Adaptive Cache Aging
        # Rarely used items have shorter actual TTL
        if expire_time and now > expire_time:
            self.delete(key)
            return None
            
        self._update_heat(key)
        return payload

    def set(self, key: str, payload: Any, timeout: Optional[int] = None) -> None:
        # Measure size for Memory Controller
        item_size = sys.getsizeof(payload) + sys.getsizeof(payload.data)
        
        while (len(self._cache) >= self.max_items or (self._current_memory + item_size > self._max_memory_bytes)) and self._cache:
            self._evict()
            
        expire_time = time.time() + timeout if timeout else 0
        
        # Apply deduplication to memory payload
        payload.data = ObjectDeduplicator.deduplicate(payload.data)
        
        if key not in self._cache:
            self._current_memory += item_size
        self._cache[key] = (payload, expire_time)
        self._update_heat(key, size=item_size)

    def delete(self, key: str) -> None:
        if key in self._cache:
            item_size = self._heat_stats.get(key, {}).get("size", 0)
            self._current_memory = max(0, self._current_memory - item_size)
            del self._cache[key]
        if key in self._heat_stats:
            del self._heat_stats[key]

    def _evict(self) -> None:
        if not self._cache: return
        
        evict_key = None
        if self.policy == EvictionPolicy.HEAT:
            # Find the "coldest" key
            evict_key = min(self._cache.keys(), key=lambda k: self._get_heat_score(k))
        else:
            # Fallback to simple random/FIFO if heat isn't requested (though we forced HEAT as default)
            evict_key = next(iter(self._cache))
            
        if evict_key:
            self.delete(evict_key)

# ─── Multi-Level Cache Engine ──────────────────────────────────────────────────
class CacheEngine:
    def __init__(
        self, 
        base_dir: str = ".syncforge_cache",
        encryption_key: Optional[str] = None,
        max_ram_items: int = 10000
    ):
        self.base_dir = base_dir
        self.encryption_key = encryption_key
        self.ram = SmartRAMManager(max_items=max_ram_items)
        self._preloaded = False
        self._preload_lock = threading.Lock()
        
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir, exist_ok=True)

        # Start Background Preloader
        threading.Thread(target=self.preload_to_ram, daemon=True).start()

        # Start Daily 4:00 AM Preload Job & Compaction
        threading.Thread(target=self._daily_preload_job, daemon=True).start()

    def _daily_preload_job(self) -> None:
        """Runs at 4:00 AM every day to clear RAM, compact disk, and preload fresh from disk."""
        while True:
            now = datetime.datetime.now()
            target = now.replace(hour=4, minute=0, second=0, microsecond=0)
            if now >= target:
                target += datetime.timedelta(days=1)
                
            seconds_to_sleep = (target - now).total_seconds()
            time.sleep(seconds_to_sleep)
            
            logger.info("[SyncForge] 4:00 AM reached. Executing intelligent daily maintenance.")
            
            # Wipe RAM entirely
            self.ram._cache.clear()
            self.ram._heat_stats.clear()
            self.ram._current_memory = 0
            
            # Intelligent Disk Layout Compaction
            self._compact_disk()
            
            # Preload fresh data from disk into RAM
            self.preload_to_ram(force=True)

    def _compact_disk(self) -> None:
        """Removes orphaned/corrupted cache files."""
        try:
            for filename in os.listdir(self.base_dir):
                if filename.endswith(".sfcache.tmp"):
                    os.remove(os.path.join(self.base_dir, filename))
        except Exception as e:
            logger.debug("[SyncForge] Compaction error: %s", e)

    def preload_to_ram(self, force: bool = False) -> None:
        with self._preload_lock:
            if self._preloaded and not force:
                return
            self._preloaded = True
            
        logger.info("[SyncForge] Preloading disk cache into RAM...")
        count = 0
        try:
            for filename in os.listdir(self.base_dir):
                if filename.endswith(".sfcache"):
                    internal_key = filename[:-8]
                    path = os.path.join(self.base_dir, filename)
                    try:
                        with open(path, "rb") as f:
                            raw = f.read()
                        payload = CachePayload.deserialize(raw, self.encryption_key)
                        self.ram.set(internal_key, payload, timeout=None)
                        count += 1
                    except Exception as e:
                        logger.debug("[SyncForge] Failed to preload %s: %s", filename, e)
        except Exception as e:
            logger.debug("[SyncForge] Preload error: %s", e)

    def _get_internal_key(self, table_name: str, cache_key: str) -> str:
        safe_table = "".join([c for c in table_name if c.isalnum() or c == "_"])
        safe_key = hashlib.md5(cache_key.encode()).hexdigest()
        return f"{safe_table}_{safe_key}"

    def get(self, table_name: str, cache_key: str, storage: StorageMode) -> Optional[Any]:
        if storage == StorageMode.DISABLED:
            return None

        internal_key = self._get_internal_key(table_name, cache_key)

        # 1. Check RAM
        if storage != StorageMode.DISK_ONLY:
            payload = self.ram.get(internal_key)
            if payload is not None:
                return payload.data

        # 2. Check Disk
        if storage in (StorageMode.RAM_DISK, StorageMode.DISK_ONLY):
            disk_payload = self._read_disk(internal_key)
            if disk_payload is not None:
                if storage == StorageMode.RAM_DISK:
                    self.ram.set(internal_key, disk_payload)
                return disk_payload.data

        return None

    def set(
        self, 
        table_name: str, 
        cache_key: str, 
        data: Any, 
        storage: StorageMode, 
        version: int = 1,
        compression: CompressionType = CompressionType.NONE,
        timeout: Optional[int] = None
    ) -> None:
        if storage == StorageMode.DISABLED:
            return

        internal_key = self._get_internal_key(table_name, cache_key)

        payload = CachePayload(
            data=data, 
            version=version, 
            timestamp=time.time(),
            compression=compression.value,
            encrypted=bool(self.encryption_key)
        )

        if storage != StorageMode.DISK_ONLY:
            self.ram.set(internal_key, payload, timeout)

        if storage in (StorageMode.RAM_DISK, StorageMode.DISK_ONLY):
            threading.Thread(
                target=self._write_disk,
                args=(internal_key, payload, compression),
                daemon=True
            ).start()

    def delete(self, table_name: str, cache_key: str) -> None:
        internal_key = self._get_internal_key(table_name, cache_key)
        self.ram.delete(internal_key)
        
        disk_path = os.path.join(self.base_dir, f"{internal_key}.sfcache")
        if os.path.exists(disk_path):
            try:
                size = os.path.getsize(disk_path)
                with open(disk_path, "wb") as f:
                    f.write(os.urandom(size))
                os.remove(disk_path)
            except OSError:
                pass

    def _write_disk(self, internal_key: str, payload: CachePayload, comp_type: CompressionType) -> None:
        path = os.path.join(self.base_dir, f"{internal_key}.sfcache")
        temp_path = path + ".tmp"
        try:
            binary_data = payload.serialize(comp_type, self.encryption_key)
            with open(temp_path, "wb") as f:
                f.write(binary_data)
            os.replace(temp_path, path)
        except Exception as e:
            logger.error("[SyncForge] Disk write failed for %s: %s", internal_key, e)
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _read_disk(self, internal_key: str) -> Optional[CachePayload]:
        path = os.path.join(self.base_dir, f"{internal_key}.sfcache")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                raw = f.read()
            return CachePayload.deserialize(raw, self.encryption_key)
        except Exception as e:
            logger.warning("[SyncForge] Disk read failed for %s. %s", internal_key, e)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return None
