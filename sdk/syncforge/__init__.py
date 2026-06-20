"""
SyncForge Python SDK
~~~~~~~~~~~~~~~~~~~~
Developer-controlled data synchronisation platform.

Create one ``SyncForge`` instance per project::

    # syncforge.py  (project root)
    import os
    from syncforge import SyncForge

    sf = SyncForge(api_key=os.environ['SYNCFORGE_API_KEY'])

Then import ``sf`` wherever you need it::

    from syncforge import sf
    sf.refresh('products')

Django integration::

    from syncforge import sf
    from syncforge.django import sync_model

    @sync_model(sf)
    class Product(models.Model):
        ...
"""

from .client import SyncForge
from .result import SyncResult
from .exceptions import (
    SyncForgeError,
    AuthError,
    TableNotFoundError,
    RateLimitError,
    NetworkError,
    ValidationError,
    ConfigurationError,
    CacheError,
)

__version__ = "1.1.0"
__author__  = "SyncForge"
__all__ = [
    "SyncForge",
    "SyncResult",
    # Exceptions
    "SyncForgeError",
    "AuthError",
    "TableNotFoundError",
    "RateLimitError",
    "NetworkError",
    "ValidationError",
    "ConfigurationError",
    "CacheError",
]
