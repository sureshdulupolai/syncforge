# SyncForge

**The developer-controlled data synchronisation platform for Python.**

SyncForge is the missing link between simple caching and complex real-time push systems. It provides an intelligent cache-aside engine with built-in stampede protection, powered by explicit developer-triggered invalidation. 

Built natively for Django, FastAPI, and Flask.

---

## The Core Concept

Instead of expiring cached data on an arbitrary timer (TTL), SyncForge lets you decide exactly when data becomes stale.

1. **Read**: `cache_query()` intercepts queries, serving them from memory.
2. **Write**: You call `sf.refresh()` after writing to the database.
3. **Invalidate**: SyncForge instantly clears the local cache and notifies the server.

The result? Zero polling, zero unnecessary database queries, and clients always get fresh data exactly when it changes.

---

## Installation

```bash
pip install syncforge
```

## Quick Start (Django)

SyncForge provides a zero-code `@sync_model` decorator for Django.

### 1. Initialize

```python
# myproject/sf.py
import os
from syncforge import SyncForge

sf = SyncForge(api_key=os.environ['SYNCFORGE_API_KEY'])
```

### 2. Auto-sync your models

```python
# models.py
from myproject.sf import sf
from syncforge.django import sync_model
from django.db import models

# On every save() or delete(), SyncForge automatically invalidates cache
@sync_model(sf, sync_mode='event')
class Product(models.Model):
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
```

### 3. Read from cache

```python
# views.py
from myproject.sf import sf
from .models import Product

def product_list(request):
    products = sf.cache_query(
        table_name='products',
        cache_key='all_active_products',
        queryset=Product.objects.filter(active=True)
    )
    return render(request, 'list.html', {'products': products})
```

---

## Security Firewall (WAF)

SyncForge SDK includes a built-in Web Application Firewall for Django. Protect your app from SQLi, XSS, and Path Traversal with one line.

```python
# settings.py
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'syncforge.middleware.SyncForgeSecurityMiddleware',  # Add this line
    # ...
]
```

## Documentation

Comprehensive guides for Django, FastAPI, Flask, and the REST API are available at:

**[https://syncforge.dev/docs/](https://syncforge.dev/docs/)**

## License

MIT License. See `LICENSE` for details.
