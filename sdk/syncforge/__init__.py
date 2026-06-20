"""
SyncForge Python SDK
~~~~~~~~~~~~~~~~~~~~
Official Python client for the SyncForge data sync platform.

Usage::

    from syncforge import SyncForge
    sf = SyncForge(api_key='sf_live_YOUR_KEY')
    sf.refresh('products')

Or use a project-level syncforge.py file (like Celery pattern)::

    # syncforge.py
    import os
    from syncforge import SyncForge
    sf = SyncForge(api_key=os.environ.get('SYNCFORGE_API_KEY'))

    # In your views / handlers:
    from syncforge import sf
    sf.refresh('products')
"""

from .client import SyncForge
from .result import SyncResult
from .exceptions import SyncForgeError, AuthError, TableNotFoundError

__version__ = "1.0.0"
__author__ = "SyncForge"
__all__ = ["SyncForge", "SyncResult", "SyncForgeError", "AuthError", "TableNotFoundError"]
