from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class SyncResult:
    """
    Returned by SyncForge.refresh().

    Attributes:
        ok              True if the sync was accepted by the server.
        table           The table name that was synced.
        project         The project name associated with the API key.
        sync_mode       Human-readable sync mode label.
        calls_saved     Cumulative database calls saved for this table.
        message         Server response message.
        raw             Full raw JSON response from the server.
        status_code     HTTP status code.
    """
    ok: bool
    table: str
    project: Optional[str] = None
    sync_mode: Optional[str] = None
    calls_saved: int = 0
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    status_code: int = 200

    def __repr__(self):
        status = "✓" if self.ok else "✗"
        return f"<SyncResult {status} table={self.table!r} calls_saved={self.calls_saved}>"
