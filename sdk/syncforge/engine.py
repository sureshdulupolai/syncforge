"""
SyncForge Enterprise Cache Management Engine
============================================

Handles multi-level caching (RAM -> Disk), smart eviction policies, 
binary serialization, compression, and AES-256 encryption.
"""

import os
import time
import pickle
import hashlib
import logging
import threading
import datetime
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

# ─── Encryption ────────────────────────────────────────────────────────────────
def encrypt_data(data: bytes, key: str) -> bytes:
    from cryptography.fernet import Fernet
    f = Fernet(key.encode('utf-8'))
    return f.encrypt(data)

def decrypt_data(data: bytes, key: str) -> bytes:
    from cryptography.fernet import Fernet
    f = Fernet(key.encode('utf-8'))
    return f.decrypt(data)

# ─── Cache Payload ─────────────────────────────────────────────────────────────
class CachePayload:
    def __init__(self, data: Any, version: int, timestamp: float, compression: str, encrypted: bool):
        self.data = data
        self.version = version
        self.timestamp = timestamp
        self.compression = compression
        self.encrypted = encrypted

    def serialize(self, comp_type: CompressionType, encryption_key: Optional[str]) -> bytes:
        raw = pickle.dumps(self.data)
        compressed = compress_data(raw, comp_type)
        if encryption_key:
            compressed = encrypt_data(compressed, encryption_key)
            self.encrypted = True
        else:
            self.encrypted = False
            
        self.compression = comp_type.value
        
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
        data = pickle.loads(raw)
        
        return cls(
            data=data,
            version=meta["version"],
            timestamp=meta["timestamp"],
            compression=meta["compression"],
            encrypted=meta["encrypted"]
        )

# ─── RAM Manager ───────────────────────────────────────────────────────────────
class SmartRAMManager:
    def __init__(self, max_items: int = 1000, policy: EvictionPolicy = EvictionPolicy.LRU):
        self.max_items = max_items
        self.policy = policy
        
        # Storage
        self._cache: Dict[str, Tuple[Any, float]] = {}  # key -> (payload, expire_time)
        
        # Policy metadata
        self._lru_order = OrderedDict()
        self._lfu_counts = {}
        self._fifo_queue = []

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
            
        payload, expire_time = self._cache[key]
        if expire_time and time.time() > expire_time:
            self.delete(key)
            return None
            
        # Update policy metadata
        if self.policy == EvictionPolicy.LRU:
            self._lru_order.move_to_end(key)
        elif self.policy == EvictionPolicy.LFU:
            self._lfu_counts[key] += 1
            
        return payload

    def set(self, key: str, payload: Any, timeout: Optional[int] = None) -> None:
        if len(self._cache) >= self.max_items and key not in self._cache:
            self._evict()
            
        expire_time = time.time() + timeout if timeout else 0
        self._cache[key] = (payload, expire_time)
        
        # Update policy metadata
        if self.policy == EvictionPolicy.LRU:
            self._lru_order[key] = True
            self._lru_order.move_to_end(key)
        elif self.policy == EvictionPolicy.LFU:
            self._lfu_counts[key] = self._lfu_counts.get(key, 0) + 1
        elif self.policy == EvictionPolicy.FIFO:
            if key not in self._fifo_queue:
                self._fifo_queue.append(key)

    def delete(self, key: str) -> None:
        if key in self._cache:
            del self._cache[key]
        if self.policy == EvictionPolicy.LRU and key in self._lru_order:
            del self._lru_order[key]
        if self.policy == EvictionPolicy.LFU and key in self._lfu_counts:
            del self._lfu_counts[key]
        if self.policy == EvictionPolicy.FIFO and key in self._fifo_queue:
            self._fifo_queue.remove(key)

    def _evict(self) -> None:
        evict_key = None
        if self.policy == EvictionPolicy.LRU:
            evict_key, _ = self._lru_order.popitem(last=False)
        elif self.policy == EvictionPolicy.LFU:
            evict_key = min(self._lfu_counts, key=self._lfu_counts.get)
        elif self.policy == EvictionPolicy.FIFO:
            evict_key = self._fifo_queue.pop(0)
            
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

        # Start Daily 4:00 AM Preload Job
        threading.Thread(target=self._daily_preload_job, daemon=True).start()

    def _daily_preload_job(self) -> None:
        """Runs at 4:00 AM every day to clear RAM and preload fresh from disk."""
        while True:
            now = datetime.datetime.now()
            target = now.replace(hour=4, minute=0, second=0, microsecond=0)
            if now >= target:
                # If it's already past 4 AM today, schedule for 4 AM tomorrow
                target += datetime.timedelta(days=1)
                
            seconds_to_sleep = (target - now).total_seconds()
            logger.info("[SyncForge] Next daily preload scheduled in %.1f seconds (at %s)", seconds_to_sleep, target.strftime("%Y-%m-%d %H:%M:%S"))
            
            # Sleep until 4:00 AM
            time.sleep(seconds_to_sleep)
            
            logger.info("[SyncForge] 4:00 AM reached. Executing daily RAM cache refresh.")
            
            # Wipe RAM entirely
            self.ram._cache.clear()
            self.ram._lru_order.clear()
            self.ram._lfu_counts.clear()
            self.ram._fifo_queue.clear()
            
            # Preload fresh data from disk into RAM
            self.preload_to_ram(force=True)

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
                    internal_key = filename[:-8] # remove .sfcache
                    path = os.path.join(self.base_dir, filename)
                    try:
                        with open(path, "rb") as f:
                            raw = f.read()
                        payload = CachePayload.deserialize(raw, self.encryption_key)
                        # Set to RAM (expires only when evicted or invalidated)
                        self.ram.set(internal_key, payload, timeout=None)
                        count += 1
                    except Exception as e:
                        logger.debug("[SyncForge] Failed to preload %s: %s", filename, e)
        except Exception as e:
            logger.debug("[SyncForge] Preload error: %s", e)
        if count > 0:
            logger.info("[SyncForge] Preloaded %d items into RAM.", count)

    def _get_internal_key(self, table_name: str, cache_key: str) -> str:
        safe_table = "".join([c for c in table_name if c.isalnum() or c == "_"])
        safe_key = hashlib.md5(cache_key.encode()).hexdigest()
        return f"{safe_table}_{safe_key}"

    def get(self, table_name: str, cache_key: str, storage: StorageMode) -> Optional[Any]:
        if storage == StorageMode.DISABLED:
            return None

        internal_key = self._get_internal_key(table_name, cache_key)

        # 1. Check RAM (skip if DISK_ONLY)
        if storage != StorageMode.DISK_ONLY:
            payload = self.ram.get(internal_key)
            if payload is not None:
                return payload.data

        # 2. Check Disk
        if storage in (StorageMode.RAM_DISK, StorageMode.DISK_ONLY):
            disk_payload = self._read_disk(internal_key)
            if disk_payload is not None:
                # Promote to RAM if allowed
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

        # 1. Write RAM synchronously (instantly available)
        if storage != StorageMode.DISK_ONLY:
            self.ram.set(internal_key, payload, timeout)

        # 2. Write Disk Asynchronously (Background)
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
                # Secure Permanent Deletion: overwrite with random bytes before unlink
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
            os.replace(temp_path, path)  # Atomic write
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
            logger.warning("[SyncForge] Disk read failed for %s (Checksum or Decrypt error). Deleting file. %s", internal_key, e)
            # Cannot use self.delete directly because we don't have table_name/cache_key, just unlink securely
            if os.path.exists(path):
                try:
                    size = os.path.getsize(path)
                    with open(path, "wb") as f:
                        f.write(os.urandom(size))
                    os.remove(path)
                except OSError:
                    pass
            return None
