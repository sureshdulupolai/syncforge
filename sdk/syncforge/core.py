"""
SyncForge — Core Engine Abstraction
===================================

Contains the universal `SyncForgeCoreAdapter` that handles metadata registration, 
cache fingerprinting, and unified caching logic. 

STRICT RULE: MUST NOT import any framework (Django, Flask, FastAPI, SQLAlchemy).
All intelligence remains here. Frameworks merely translate their hooks to call this.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Type

from .events import SyncForgeEvent, emit_event

logger = logging.getLogger("syncforge.core")

class SyncForgeCoreAdapter:
    """
    The unified engine that abstracts all caching and synchronization intelligence.
    Framework adapters simply bind their events (e.g. post_save, after_update) to this core.
    """
    
    def __init__(self, sf_client: Any):
        self.sf_client = sf_client
        self._registered_tables = set()

    def register_model(
        self,
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
        local_metadata_updater: Optional[Callable] = None,
        local_metadata_fetcher: Optional[Callable] = None,
    ) -> None:
        """
        Unified registration logic called by all framework decorators.
        """
        if table_name in self._registered_tables:
            logger.debug(f"[SyncForge Core] '{table_name}' already registered — skipping.")
            return

        if waf_enabled:
            self.sf_client.register_waf_config(table_name, max_requests, block_time_sec)

        # 1. Register with Remote SyncForge Dashboard
        try:
            self.sf_client.create_table(
                table_name,
                sync_mode=sync_mode,
                active=active,
                storage_mode=storage_mode,
                compression=compression,
                encryption=encryption,
                priority=priority,
                refresh_interval=refresh_interval,
                timeout=timeout
            )
            
            # 2. Update Local Metadata Storage (Framework-specific adapter injects this)
            if local_metadata_updater:
                local_metadata_updater(
                    table_name=table_name,
                    storage_mode=storage_mode,
                    compression=compression,
                    encryption=encryption,
                    status="active" if active else "inactive"
                )
            
            logger.info(f"[SyncForge Core] Registered table '{table_name}' (mode={sync_mode}).")
        except Exception as exc:
            logger.warning(
                f"[SyncForge Core] Could not register table '{table_name}' on server: {exc}. "
                "Local cache invalidation will still work."
            )

        # 3. Configure Background Scheduler for Remote Invalidation Checks
        if self.sf_client.scheduler is None and local_metadata_updater and local_metadata_fetcher:
            def fetch_remote_metadata(table_names: list[str]) -> dict[str, dict]:
                try:
                    remote_tables = self.sf_client.list_tables()
                    remote_map = {t["table_name"]: t for t in remote_tables}
                except Exception as e:
                    logger.debug(f"[SyncForge Scheduler] Failed to fetch remote metadata: {e}")
                    remote_map = {}

                local_records = local_metadata_fetcher(table_names)
                merged = {}
                for name in table_names:
                    local_meta = local_records.get(name, {})
                    remote_meta = remote_map.get(name, {})
                    
                    remote_version = remote_meta.get("cache_version", 1)
                    local_version = local_meta.get("cache_version", 1)
                    
                    m_active = remote_meta.get("active", local_meta.get("active", True))
                    m_storage_mode = remote_meta.get("storage_mode", local_meta.get("storage_mode", "ram_disk"))
                    m_compression = remote_meta.get("compression", local_meta.get("compression", "none"))
                    m_encryption = remote_meta.get("encryption", local_meta.get("encryption", False))
                    m_timeout = remote_meta.get("timeout", local_meta.get("timeout", 3600))
                    
                    if remote_version > local_version:
                        local_metadata_updater(
                            table_name=name,
                            cache_version=remote_version,
                            storage_mode=m_storage_mode,
                            compression=m_compression,
                            encryption=m_encryption,
                            timeout=m_timeout,
                            status="active" if m_active else "inactive"
                        )
                        current_version = remote_version
                    else:
                        current_version = local_version
                        
                    merged[name] = {
                        "storage_mode": m_storage_mode,
                        "compression": m_compression,
                        "encryption": m_encryption,
                        "timeout": m_timeout,
                        "cache_version": current_version,
                        "active": m_active
                    }
                return merged

            self.sf_client.configure_scheduler(fetch_remote_metadata)

        if self.sf_client.scheduler:
            self.sf_client.scheduler.add_table(table_name)

        self._registered_tables.add(table_name)

    def trigger_sync(self, table_name: str) -> None:
        """
        Universal synchronization trigger.
        Frameworks must call this when a write/update/delete happens.
        """
        import threading
        
        emit_event(SyncForgeEvent.REFRESH_TRIGGER, table=table_name)
        
        # 1. Invalidate local cache (fast, synchronous)
        self.sf_client.store_manager.invalidate_table_registry(table_name)

        # 2. Notify SyncForge server (slow, async)
        thread = threading.Thread(
            target=self._notify_server,
            args=(table_name,),
            daemon=True,
            name=f"sf-sync-{table_name}",
        )
        thread.start()

    def _notify_server(self, table_name: str) -> None:
        try:
            self.sf_client.refresh(table_name)
            logger.debug(f"[SyncForge Core] Server notified of change in table '{table_name}'.")
        except Exception as exc:
            logger.error(
                f"[SyncForge Core] Failed to notify server of change in table '{table_name}': {exc}. "
                "Local cache has already been invalidated."
            )
