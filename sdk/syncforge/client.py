"""
SyncForge Python SDK — Client
"""
from __future__ import annotations

import threading
import urllib.request
import urllib.error
import json
import time
from typing import Optional, Union, List

from .result import SyncResult
from .exceptions import (
    SyncForgeError, AuthError, TableNotFoundError,
    RateLimitError, NetworkError,
)

# Default production base URL — override for self-hosted or local dev
DEFAULT_BASE_URL = "https://syncforge.dev/api"

# Per-request timeout (seconds)
DEFAULT_TIMEOUT = 10


class SyncForge:
    """
    Official Python client for the SyncForge data sync platform.

    Create one instance per project (API key) — typically in a
    ``syncforge.py`` file at your project root, then import it
    wherever needed::

        # syncforge.py (project root)
        import os
        from syncforge import SyncForge

        sf = SyncForge(api_key=os.environ.get('SYNCFORGE_API_KEY', 'sf_live_...'))

        # views.py / routes.py / any handler
        from syncforge import sf
        sf.refresh('products')

    Args:
        api_key:    Your SyncForge API key (starts with ``sf_live_``).
        base_url:   Override the base URL (useful for local dev / self-hosted).
        timeout:    HTTP timeout in seconds (default: 10).
        silent:     If ``True``, all errors are suppressed and logged instead
                    of raised. Use this in production so a SyncForge outage
                    never breaks your app flow. Default: ``False``.
        async_mode: If ``True``, every ``refresh()`` call runs in a background
                    thread and returns immediately. Default: ``False``.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        silent: bool = False,
        async_mode: bool = False,
    ):
        if not api_key:
            raise ValueError("api_key is required.")

        self._api_key   = api_key.strip()
        self._base_url  = base_url.rstrip("/")
        self._timeout   = timeout
        self._silent    = silent
        self._async     = async_mode

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self, *tables: str) -> Union[SyncResult, List[SyncResult], None]:
        """
        Trigger a data sync for one or more tables.

        After any database write, call this to tell SyncForge to
        broadcast the change to all connected clients.

        Args:
            *tables: One or more table names registered in your SyncForge
                     dashboard (e.g. ``'products'``, ``'orders'``).

        Returns:
            A :class:`SyncResult` for a single table, or a list of
            :class:`SyncResult` objects when multiple tables are passed.
            Returns ``None`` when ``async_mode=True`` (fire-and-forget).

        Raises:
            :class:`AuthError`:           Invalid or missing API key.
            :class:`TableNotFoundError`:  Table not registered in dashboard.
            :class:`NetworkError`:        Connection / timeout failure.
            :class:`SyncForgeError`:      Any other server-side error.

        Examples::

            # Single table
            sf.refresh('products')

            # Multiple tables at once
            sf.refresh('products', 'categories', 'inventory')

            # With result inspection
            result = sf.refresh('products')
            if result.ok:
                print(f"{result.calls_saved} DB calls saved!")
        """
        if not tables:
            raise ValueError("At least one table name is required.")

        if self._async:
            t = threading.Thread(
                target=self._refresh_all,
                args=(tables,),
                daemon=True,
            )
            t.start()
            return None

        results = self._refresh_all(tables)
        return results[0] if len(results) == 1 else results

    def ping(self) -> bool:
        """
        Check that your API key is valid and SyncForge is reachable.

        Returns:
            ``True`` if the health endpoint responds, ``False`` otherwise.
        """
        try:
            url = f"{self._base_url}/v1/health/"
            self._request("GET", url)
            return True
        except Exception:
            return False

    def project_info(self) -> dict:
        """
        Fetch project metadata and registered tables for this API key.

        Returns:
            dict with ``project``, ``slug``, ``tables``, ``active_keys``.
        """
        url = f"{self._base_url}/v1/project/"
        return self._request("GET", url)

    def list_tables(self) -> list:
        """
        Return all tables registered in this project.

        Returns:
            List of dicts with ``table_name``, ``sync_mode``, ``rows_count``,
            ``database_calls_saved``.
        """
        url = f"{self._base_url}/v1/tables/"
        data = self._request("GET", url)
        return data.get("tables", [])

    def create_table(self, table_name: str, sync_mode: str = "event") -> bool:
        """
        Register a new table programmatically.
        
        Args:
            table_name: The name of the table to register.
            sync_mode: The sync mode (e.g. 'event', 'manual', 'schedule_5m').
            
        Returns:
            bool: True if created, False if it already existed.
        """
        table_name = table_name.strip().lower()
        if not table_name:
            raise ValueError("Table name cannot be empty.")
            
        url = f"{self._base_url}/v1/tables/"
        try:
            res = self._request("POST", url, json_data={"table_name": table_name, "sync_mode": sync_mode})
            return res.get("created", False)
        except SyncForgeError as exc:
            if self._silent:
                import warnings
                warnings.warn(f"[SyncForge] create_table failed: {exc}", stacklevel=2)
                return False
            raise

    def delete_table(self, table_name: str) -> bool:
        """
        Delete a registered table from the SyncForge dashboard programmatically.
        
        Args:
            table_name: The name of the table to delete.
            
        Returns:
            bool: True if it was deleted, False otherwise.
        """
        table_name = table_name.strip().lower()
        if not table_name:
            raise ValueError("Table name cannot be empty.")
            
        import urllib.parse
        url = f"{self._base_url}/v1/tables/?table_name={urllib.parse.quote(table_name)}"
        try:
            res = self._request("DELETE", url)
            return res.get("deleted", False)
        except SyncForgeError as exc:
            if self._silent:
                import warnings
                warnings.warn(f"[SyncForge] delete_table failed: {exc}", stacklevel=2)
                return False
            raise

    # ── Internal ──────────────────────────────────────────────────────────────

    def _refresh_all(self, tables: tuple) -> List[SyncResult]:
        results = []
        for table in tables:
            try:
                result = self._sync_one(table)
                results.append(result)
            except SyncForgeError as exc:
                if self._silent:
                    import warnings
                    warnings.warn(f"[SyncForge] {exc} (table={table!r})", stacklevel=4)
                    results.append(SyncResult(
                        ok=False, table=table, message=str(exc),
                        status_code=getattr(exc, 'status_code', None) or 0,
                    ))
                else:
                    raise
        return results

    def _sync_one(self, table: str) -> SyncResult:
        table = table.strip().lower()
        if not table:
            raise ValueError("Table name cannot be empty.")

        url  = f"{self._base_url}/v1/sync/{table}/"
        data = self._request("POST", url)

        return SyncResult(
            ok          = data.get("status") == "ok",
            table       = data.get("table", table),
            project     = data.get("project"),
            sync_mode   = data.get("sync_mode"),
            calls_saved = data.get("database_calls_saved", 0),
            message     = data.get("message", ""),
            raw         = data,
            status_code = 200,
        )

    def _request(self, method: str, url: str, json_data: dict = None) -> dict:
        headers = {
            "X-API-Key":    self._api_key,
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   "syncforge-python/1.0.1",
        }
        body = b"" if method == "GET" else b"{}"
        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")
        req  = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}

        except urllib.error.HTTPError as exc:
            raw  = exc.read().decode("utf-8", errors="replace")
            data = {}
            try:
                data = json.loads(raw)
            except Exception:
                pass

            error_msg = data.get("error", raw or exc.reason)
            code      = exc.code

            if code == 401:
                raise AuthError(f"Invalid API key: {error_msg}", status_code=code)
            if code == 404:
                raise TableNotFoundError(
                    f"Table not found — register it in your SyncForge dashboard. {error_msg}",
                    status_code=code,
                )
            if code == 429:
                raise RateLimitError(f"Rate limit exceeded: {error_msg}", status_code=code)
            raise SyncForgeError(f"Server error {code}: {error_msg}", status_code=code)

        except urllib.error.URLError as exc:
            raise NetworkError(
                f"Could not connect to SyncForge ({url}): {exc.reason}"
            ) from exc

        except TimeoutError:
            raise NetworkError(
                f"Request to SyncForge timed out after {self._timeout}s."
            )
