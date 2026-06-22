"""
SyncForge Smart Scheduler
=========================

Runs a background daemon to periodically check for metadata updates.
This minimizes database load by fetching ONLY the `syncforge_metadata` rows
instead of polling the actual data tables.
"""

import threading
import time
import logging
from typing import Callable, List, Dict, Any, Optional

logger = logging.getLogger("syncforge.scheduler")

class SmartScheduler:
    def __init__(
        self, 
        fetch_metadata_fn: Callable[[List[str]], Dict[str, Dict[str, Any]]],
        invalidate_fn: Callable[[str], None],
        reload_fn: Callable[[str], None],
        check_interval_seconds: int = 60
    ):
        """
        Args:
            fetch_metadata_fn: A framework-specific function (Django/SQLAlchemy) 
                that returns metadata dicts keyed by table_name.
            invalidate_fn: Function to invalidate cache when a refresh is detected.
            reload_fn: Function to optionally reload the cache (if preloading is enabled).
            check_interval_seconds: How often to poll the metadata table.
        """
        self.fetch_metadata_fn = fetch_metadata_fn
        self.invalidate_fn = invalidate_fn
        self.reload_fn = reload_fn
        self.check_interval_seconds = check_interval_seconds
        
        self.tables_to_watch: List[str] = []
        self._versions: Dict[str, int] = {}
        
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def add_table(self, table_name: str) -> None:
        if table_name not in self.tables_to_watch:
            self.tables_to_watch.append(table_name)
            logger.debug("[SyncForge Scheduler] Now watching table: %s", table_name)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
            
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="sf-scheduler",
            daemon=True
        )
        self._thread.start()
        logger.info("[SyncForge Scheduler] Background daemon started. Interval: %ds", self.check_interval_seconds)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        # Initial wait to let app boot up
        if self._stop_event.wait(5.0):
            return
            
        while not self._stop_event.is_set():
            try:
                self._check_metadata()
            except Exception as e:
                logger.error("[SyncForge Scheduler] Error during metadata check: %s", e)
                
            # Sleep in small increments to allow quick shutdown
            for _ in range(self.check_interval_seconds):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def _check_metadata(self) -> None:
        if not self.tables_to_watch:
            return
            
        # Fetch metadata for all watched tables in a SINGLE lightweight query
        metadata_map = self.fetch_metadata_fn(self.tables_to_watch)
        
        for table, meta in metadata_map.items():
            current_version = meta.get("cache_version", 1)
            refresh_flag = meta.get("refresh_flag", False)
            active = meta.get("active", True)
            
            if not active:
                continue
                
            known_version = self._versions.get(table)
            
            # If version changed or explicit refresh flag is set
            if refresh_flag or (known_version is not None and current_version > known_version):
                logger.info("[SyncForge Scheduler] Refresh detected for table: %s (Version: %s)", table, current_version)
                
                # Invalidate existing cache
                self.invalidate_fn(table)
                
                # Pre-reload the cache to prevent cache miss latency on next request
                self.reload_fn(table)
                
            # Update known version
            self._versions[table] = current_version
