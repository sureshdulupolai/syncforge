"""
SyncForge — SQLAlchemy Integration
====================================

Provides event listeners and metadata tracking for SQLAlchemy models.
"""
from __future__ import annotations

import logging
import threading
from typing import Set, Type, List, Dict, Callable

logger = logging.getLogger("syncforge.sqlalchemy")

_registered_tables: Set[str] = set()
_registration_lock = threading.Lock()

try:
    from sqlalchemy import Column, String, Boolean, BigInteger, DateTime, event
    from sqlalchemy.orm import declarative_base
    from sqlalchemy.sql import func
    HAS_SQLALCHEMY = True
    Base = declarative_base()
except ImportError:
    HAS_SQLALCHEMY = False
    Base = object

# ── Metadata Model ─────────────────────────────────────────────────────────────

if HAS_SQLALCHEMY:
    class SyncforgeMetadata(Base):
        __tablename__ = "syncforge_metadata"

        table_name = Column(String(255), primary_key=True)
        storage_mode = Column(String(50), default="ram_disk")
        compression = Column(String(50), default="none")
        encryption = Column(Boolean, default=False)
        cache_version = Column(BigInteger, default=1)
        last_accessed = Column(DateTime, server_default=func.now(), onupdate=func.now())
        size_bytes = Column(BigInteger, default=0)
        status = Column(String(50), default="active")

    def update_local_metadata(session, table_name: str, **kwargs):
        """Helper to safely update the local metadata table."""
        try:
            record = session.query(SyncforgeMetadata).filter_by(table_name=table_name).first()
            if not record:
                record = SyncforgeMetadata(table_name=table_name, **kwargs)
                session.add(record)
            else:
                for k, v in kwargs.items():
                    setattr(record, k, v)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.debug("[SyncForge] Failed to update local metadata: %s", e)

    def fetch_sqlalchemy_metadata(session_maker: Callable) -> Callable:
        """Returns a fetch_metadata_fn for SmartScheduler."""
        def fetch_metadata(table_names: List[str]) -> Dict[str, dict]:
            session = session_maker()
            try:
                records = session.query(SyncforgeMetadata).filter(SyncforgeMetadata.table_name.in_(table_names)).all()
                return {
                    r.table_name: {
                        "storage_mode": r.storage_mode,
                        "compression": r.compression,
                        "encryption": r.encryption,
                        "cache_version": r.cache_version,
                        "active": r.status == "active"
                    }
                    for r in records
                }
            except Exception as e:
                logger.debug("[SyncForge] Failed to fetch local metadata: %s", e)
                return {}
            finally:
                session.close()
        return fetch_metadata

# ── Sync Event Listener ────────────────────────────────────────────────────────

def _trigger_sync(sf_client, table_name: str):
    sf_client._invalidate_local_cache(table_name)
    thread = threading.Thread(
        target=sf_client.refresh,
        args=(table_name,),
        daemon=True,
        name=f"sf-sync-{table_name}",
    )
    thread.start()

def sync_model(
    sf_client, 
    sync_mode: str = "event",
    active: bool = True,
    storage_mode: str = "ram_disk",
    compression: str = "none",
    encryption: bool = True,
    priority: str = "medium",
    refresh_interval: int = 0,
    session_maker=None
):
    """
    Class decorator for SQLAlchemy declarative models.
    Registers the model with SyncForge and attaches event listeners for 
    after_insert, after_update, and after_delete.
    """
    def decorator(cls: Type) -> Type:
        if not HAS_SQLALCHEMY:
            raise ImportError("SQLAlchemy is not installed.")

        table_name = getattr(cls, "__tablename__", cls.__name__.lower())

        with _registration_lock:
            if table_name in _registered_tables:
                return cls

            try:
                sf_client.create_table(
                    table_name, 
                    sync_mode=sync_mode,
                    active=active,
                    storage_mode=storage_mode,
                    compression=compression,
                    encryption=encryption,
                    priority=priority,
                    refresh_interval=refresh_interval
                )
                
                if session_maker:
                    session = session_maker()
                    try:
                        update_local_metadata(
                            session,
                            table_name,
                            storage_mode=storage_mode,
                            compression=compression,
                            encryption=encryption,
                            status="active" if active else "inactive"
                        )
                    finally:
                        session.close()
                        
                logger.info("[SyncForge] Registered SQLAlchemy table '%s' (mode=%s).", table_name, sync_mode)
                print(f"\033[92m🚀 [SyncForge] Successfully registered table '{table_name}' (mode={sync_mode}, storage={storage_mode}, enc={encryption})\033[0m")
            except Exception as exc:
                logger.warning(
                    "[SyncForge] Could not register table '%s' on SyncForge server: %s.",
                    table_name, exc,
                )
                print(f"\033[91m❌ [SyncForge] Failed to register table '{table_name}': {exc}\033[0m")

            # Hook into SQLAlchemy events
            def _after_change(mapper, connection, target):
                _trigger_sync(sf_client, table_name)

            event.listen(cls, 'after_insert', _after_change)
            event.listen(cls, 'after_update', _after_change)
            event.listen(cls, 'after_delete', _after_change)

            _registered_tables.add(table_name)

        return cls
    return decorator
