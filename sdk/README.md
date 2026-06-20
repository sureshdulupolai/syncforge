# SyncForge Python SDK

[![PyPI](https://img.shields.io/pypi/v/syncforge)](https://pypi.org/project/syncforge/)
[![Python](https://img.shields.io/pypi/pyversions/syncforge)](https://pypi.org/project/syncforge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Official Python SDK for the [SyncForge](https://syncforge.dev) data sync platform.  
Control exactly when data syncs between your database and client applications — no polling, no wasted DB calls.

---

## Installation

```bash
pip install syncforge
```

**Zero external dependencies.** Uses only Python stdlib (`urllib`, `json`, `threading`).

---

## Quick Start

```python
from syncforge import SyncForge

sf = SyncForge(api_key='sf_live_YOUR_KEY')

# After any DB write — notify all connected clients
sf.refresh('products')
```

---

## The `syncforge.py` Pattern (Recommended)

Place a `syncforge.py` file at your project root — same level as `manage.py` or `main.py`.  
This mirrors the Celery pattern and gives you a single shared instance.

```python
# syncforge.py (project root)
import os
from syncforge import SyncForge

sf = SyncForge(
    api_key=os.environ.get('SYNCFORGE_API_KEY', 'sf_live_YOUR_KEY')
)
```

Then import `sf` anywhere:

```python
# views.py / routes.py
from syncforge import sf

def create_product(request):
    Product.objects.create(name='New Item', price=99.99)
    sf.refresh('products')           # one line — all clients updated
    return JsonResponse({'status': 'created'})
```

---

## API Reference

### `SyncForge(api_key, base_url, timeout, silent, async_mode)`

| Parameter    | Default                        | Description                                          |
|--------------|-------------------------------|------------------------------------------------------|
| `api_key`    | required                       | Your API key (`sf_live_...`)                         |
| `base_url`   | `https://syncforge.dev/api`   | Override for local dev / self-hosted                 |
| `timeout`    | `10`                           | HTTP timeout in seconds                              |
| `silent`     | `False`                        | Suppress errors — logs warnings instead of raising   |
| `async_mode` | `False`                        | Fire-and-forget — refresh runs in a background thread|

### `sf.refresh(*tables)` → `SyncResult | list[SyncResult]`

```python
sf.refresh('products')                         # single table
sf.refresh('products', 'categories', 'orders') # multiple at once

result = sf.refresh('products')
print(result.ok)           # True
print(result.calls_saved)  # 1854211
print(result.sync_mode)    # 'Event — On INSERT / UPDATE / DELETE'
```

### `sf.ping()` → `bool`
Health check — returns `True` if SyncForge is reachable.

### `sf.project_info()` → `dict`
Returns project metadata and all registered tables.

### `sf.list_tables()` → `list`
Lists all tables with their sync mode and stats.

---

## Django Integration

```python
# syncforge.py (next to manage.py)
import os
from syncforge import SyncForge
sf = SyncForge(api_key=os.environ.get('SYNCFORGE_API_KEY'))

# myapp/views.py
from syncforge import sf

def update_products(request):
    Product.objects.filter(on_sale=True).update(price=F('price') * 0.9)
    sf.refresh('products')
    return JsonResponse({'status': 'updated'})
```

## FastAPI Integration

```python
from fastapi import FastAPI
from syncforge import sf

app = FastAPI()

@app.post("/products/")
async def create_product(name: str, price: float):
    db.execute("INSERT INTO products ...")
    sf.refresh('products')
    return {"status": "ok"}
```

---

## Production Tips

```python
# Silent mode — SyncForge errors never crash your app
sf = SyncForge(api_key='sf_live_...', silent=True)

# Async mode — fire-and-forget, returns immediately
sf = SyncForge(api_key='sf_live_...', async_mode=True)
sf.refresh('products')  # returns None, syncs in background

# Override base URL for local development
sf = SyncForge(api_key='sf_live_...', base_url='http://localhost:8000/api')
```

---

## Error Handling

```python
from syncforge import SyncForge, AuthError, TableNotFoundError, NetworkError

sf = SyncForge(api_key='sf_live_...')
try:
    sf.refresh('products')
except AuthError:
    print("Invalid API key")
except TableNotFoundError:
    print("Register the table in your SyncForge dashboard first")
except NetworkError:
    print("Could not reach SyncForge — check your internet connection")
```

---

## License

MIT — see [LICENSE](LICENSE)
