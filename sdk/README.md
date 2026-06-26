# SyncForge Python SDK

[![PyPI version](https://img.shields.io/pypi/v/syncforge.svg)](https://pypi.org/project/syncforge/)
[![Python versions](https://img.shields.io/pypi/pyversions/syncforge.svg)](https://pypi.org/project/syncforge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Created by Suresh Dullu Polai**

SyncForge is a **developer-controlled data synchronisation platform**. It
reduces unnecessary database reads by intelligently serving previously fetched
data until the developer explicitly signals that the underlying data has changed.

```
Developer writes to database
          ↓
sf.refresh("products")
          ↓
SyncForge invalidates cached data
          ↓
Next request fetches fresh data from DB → cached again
```

Unlike time-based expiration, **the developer controls when data becomes stale**
— not a TTL clock.

---

## How It Works

```
First request
─────────────
Client → cache_query() → cache MISS → DB query → store in cache → return data

Subsequent requests (until refresh)
────────────────────────────────────
Client → cache_query() → cache HIT → return data  ← no DB query

After sf.refresh("products")
─────────────────────────────
cache_query() → cache MISS again → DB query → store → return fresh data
```

---

## Installation

```bash
pip install syncforge
```

No external dependencies — the SDK is built entirely on the Python Standard Library
(`urllib`, `json`, `threading`, `hashlib`, `hmac`).

---

## Quick Start

### Step 1 — Initialise the client

```python
# syncforge.py  (project root — one instance per project)
import os
from syncforge import SyncForge

sf = SyncForge(api_key=os.environ['SYNCFORGE_API_KEY'])
```

### Step 2 — Signal data changes

```python
# After any database write, call sf.refresh()
from syncforge import sf

product = Product.objects.create(name="Widget", price=9.99)
sf.refresh("products")   # Tell SyncForge this table changed
```

### Step 3 — Read with cache (optional)

```python
# cache_query serves from cache; hits DB only on miss or after refresh
products = sf.cache_query(
    table_name="core_product",
    queryset=Product.objects.filter(active=True).order_by("name"),
    timeout=3600,   # Fallback TTL in seconds (None = developer-only invalidation)
)
```

---

## Django Integration

### Automatic sync with `@sync_model`

```python
# models.py
from django.db import models
from syncforge import sf
from syncforge.django import sync_model

@sync_model(sf, sync_mode="event")
class Product(models.Model):
    name  = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = "core_product"
```

`@sync_model` hooks into Django's `post_save` and `post_delete` signals.
When a `Product` is created, updated, or deleted:

1. The local cache for `core_product` is **immediately invalidated** (synchronous).
2. The SyncForge server is **notified asynchronously** (background thread — never
   blocks the request or the database write).

### Using the cached data in a view

```python
# views.py
from django.shortcuts import render
from syncforge import sf
from .models import Product

def product_list(request):
    products = sf.cache_query(
        table_name="core_product",
        queryset=Product.objects.filter(active=True).order_by("name"),
        timeout=3600,
    )
    return render(request, "products/list.html", {
        "products": products,          # list of Product model instances
        "count":    len(products),
    })
```

```html
<!-- products/list.html -->
{% for product in products %}
    <div>{{ product.name }} — ₹{{ product.price }}</div>
{% empty %}
    <p>No products found.</p>
{% endfor %}
```

> `cache_query` returns a standard Python `list` of Django model instances —
> identical to `list(Product.objects.filter(...))`. All model fields and
> methods are accessible.

### Security middleware (WAF + response headers)

```python
# settings.py
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'syncforge.middleware.SyncForgeSecurityMiddleware',  # ← add here
    'django.contrib.sessions.middleware.SessionMiddleware',
    # ...
]
```

This adds:

| Feature | Detail |
|---|---|
| Basic WAF | Regex-based scan for SQLi, XSS, path traversal in URL + POST body |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `SAMEORIGIN` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | Disables camera, mic, geolocation, payment |
| `Content-Security-Policy` | Restrictive default; configurable |
| `Strict-Transport-Security` | Injected over HTTPS connections |
| Request timing | Logs method, path, status, duration (ms) |

> **Note**: The WAF provides defence-in-depth against common, unsophisticated
> attacks. It does not replace Django's CSRF protection, ORM parameterisation,
> or template auto-escaping. Always validate and sanitise input in your
> application code.

### Auto Maintenance Middleware (4 AM IST Cleanup)

SyncForge automatically calculates cache expiration at 4:00 AM IST. To ensure old cache files and RAM memory are safely and instantly garbage-collected at exactly 4:00 AM without relying on a sleeping background thread, add the Maintenance Middleware:

```python
# settings.py
MIDDLEWARE = [
    # ...
    'syncforge.middleware.SyncForgeMaintenanceMiddleware',
]
```

This middleware performs a single `time.time()` float comparison on every request, ensuring **zero performance overhead (a few nanoseconds)**. It performs the actual cleanup asynchronously without delaying user requests.

---

## FastAPI / Flask Integration

SyncForge works with any Python web framework:

### FastAPI

```python
import os
from fastapi import FastAPI
from syncforge import SyncForge
from syncforge.fastapi import SyncForgeMaintenanceMiddleware

sf  = SyncForge(api_key=os.environ['SYNCFORGE_API_KEY'])
app = FastAPI()

# Ultra-fast auto-maintenance at 4 AM IST
app.add_middleware(SyncForgeMaintenanceMiddleware, sf_client=sf)

@app.post("/products/")
async def create_product(name: str, price: float):
    db.execute("INSERT INTO products (name, price) VALUES (?, ?)", (name, price))
    sf.refresh("products")   # Signal data change
    return {"status": "created"}
```

### Flask

```python
import os
from flask import Flask
from syncforge import SyncForge
from syncforge.flask import SyncForgeFlask

sf  = SyncForge(api_key=os.environ['SYNCFORGE_API_KEY'])
app = Flask(__name__)

# SyncForgeFlask automatically registers the WAF and Maintenance hooks
sf_ext = SyncForgeFlask(app, sf)

@app.post("/products/")
def create_product():
    db.execute("INSERT INTO products ...")
    sf.refresh("products")
    return {"status": "created"}
```

### Plain Python

```python
import os
from syncforge import SyncForge

sf = SyncForge(api_key=os.environ['SYNCFORGE_API_KEY'])
sf.refresh("products")
```

---

## API Reference

### `SyncForge(api_key, base_url, timeout, silent, async_mode, sign_requests)`

| Parameter | Default | Description |
|---|---|---|
| `api_key` | required | API key from your dashboard (`sf_live_...`) |
| `base_url` | `https://syncforge.dev/api` | Override for local dev or self-hosted |
| `timeout` | `10` | HTTP timeout (seconds) per request |
| `silent` | `False` | Suppress exceptions — logs warnings instead |
| `async_mode` | `False` | Fire-and-forget `refresh()` — returns `None` immediately |
| `sign_requests` | `True` | Add HMAC-SHA256 signature headers for replay protection |

### `sf.refresh(*tables)` → `SyncResult | list[SyncResult]`

Signal that data has changed in one or more tables.

```python
sf.refresh("products")                              # single table
sf.refresh("products", "categories", "inventory")  # multiple tables

result = sf.refresh("products")
print(result.ok)            # True/False
print(result.calls_saved)   # DB reads saved (from server analytics)
print(result.version_number)# Current table version
```

### `sf.cache_query(table_name, cache_key, queryset, timeout)` → `list`

Serve data from cache; query the database on miss or after invalidation.

```python
data = sf.cache_query(
    table_name="core_product",
    queryset=Product.objects.filter(active=True),
    timeout=3600,    # seconds; None = no automatic expiration
)
```

### `sf.ping()` → `bool`

Check connectivity and API key validity.

### `sf.list_tables()` → `list[dict]`

Return all registered tables and their statistics.

### `sf.create_table(table_name, sync_mode)` → `bool`

Register a table programmatically (called automatically by `@sync_model`).

---

## Timeout Strategy

| Use Case | Configuration |
|---|---|
| Standard data (changes regularly) | `timeout=3600` (1 hour) |
| Developer-only invalidation | `timeout=None` + `sf.refresh()` after writes |
| Monthly refresh | `timeout=None`, rotate query dynamically by year/month |
| Permanent static data | `timeout=None`, never call `sf.refresh()` |

### Monthly rotation example

```python
import datetime

now = datetime.date.today()
data = sf.cache_query(
    table_name="core_config",
    queryset=Config.objects.filter(active=True, created__year=now.year, created__month=now.month),
    timeout=None,
)
```

---

## Production Deployment

### Redis (Required for multi-worker deployments)

With Gunicorn / uWSGI running multiple workers, each worker has its own
in-process memory. Without a shared cache backend, `sf.refresh()` in Worker 1
will not invalidate caches in Workers 2, 3, and 4.

**Configure Redis** as the Django cache backend:

```python
# settings.py
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/1"),
    }
}
```

The SDK emits a `RuntimeWarning` when `LocMemCache` is detected, helping you
catch this misconfiguration during development.

### Silent mode (recommended in production)

```python
sf = SyncForge(
    api_key=os.environ['SYNCFORGE_API_KEY'],
    silent=True,   # SyncForge errors are logged, not raised
)
```

With `silent=True`, a SyncForge service interruption (network issue, outage)
will never propagate an exception to your users. The SDK logs a warning and
your application continues normally — it will simply make more database queries
until connectivity is restored.

### Async mode

```python
sf = SyncForge(
    api_key=os.environ['SYNCFORGE_API_KEY'],
    async_mode=True,   # refresh() returns None immediately
)
sf.refresh("products")  # Runs in background daemon thread
```

---

## Security Best Practices

### Never commit your API key

```python
# ❌ Never do this
sf = SyncForge(api_key="sf_live_abc123xyz")

# ✅ Always read from environment
import os
sf = SyncForge(api_key=os.environ['SYNCFORGE_API_KEY'])
```

Keep your key in a `.env` file locally and set it as an environment variable
in your hosting platform. Never commit `.env` to version control.

### Use per-client cache keys

```python
# ❌ One cache key for all users
cache_key = "products"

# ✅ Per-user/per-client keys prevent data leakage
cache_key = f"products_client_{request.user.client_id}"
```

### Do not cache sensitive data

```python
# ❌ Never cache passwords, tokens, or PII
queryset = User.objects.values("username", "password_hash", "auth_token")

# ✅ Cache only public/display-safe fields
queryset = Product.objects.filter(active=True).values("id", "name", "price")
```

---

## Limitations

| Limitation | Explanation |
|---|---|
| Multi-process requires Redis | `LocMemCache` does not share state across Gunicorn workers |
| WAF is not a complete security solution | Provides basic pattern matching; does not replace proper input validation |
| `cache_query` returns a list | The queryset is fully evaluated; lazy loading is not preserved |
| Stampede protection is per-process | Thread locks prevent stampedes within one worker; Redis lock needed across workers |
| `sf.refresh()` is synchronous by default | Use `async_mode=True` or the `@sync_model` decorator (which is always async) for non-blocking calls |

---

## Troubleshooting

**`RuntimeWarning: LocMemCache detected`**
→ Configure Redis as your cache backend for multi-process deployments.

**`AuthError: Authentication failed`**
→ Your API key is invalid or revoked. Generate a new key in your dashboard.

**`TableNotFoundError`**
→ The table is not registered. Use `@sync_model` or `sf.create_table()`.

**`NetworkError: Could not connect to SyncForge`**
→ Check your network and `base_url`. Use `silent=True` in production.

**Stale data after `sf.refresh()`**
→ Most likely a multi-worker deployment without Redis. See above.

---

## License

MIT — see [LICENSE](LICENSE)
