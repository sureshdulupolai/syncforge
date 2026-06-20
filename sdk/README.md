# SyncForge Python SDK

[![PyPI version](https://img.shields.io/pypi/v/syncforge.svg)](https://pypi.org/project/syncforge/)
[![Python versions](https://img.shields.io/pypi/pyversions/syncforge.svg)](https://pypi.org/project/syncforge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Created by Suresh Dullu Polai**

SyncForge is the **FastAPI of data synchronization**. It is a premium, developer-controlled smart data synchronization platform and Web Application Firewall (WAF). 

Stop relying on dumb polling or expensive real-time sockets for static data. With SyncForge, you take full control over exactly when and how your applications sync data, saving millions of unnecessary database calls and drastically reducing server costs.

---

## 🌟 Best Features (A to Z)

- **Zero-Polling Architecture**: Clients never hit your database to check for updates. They only get notified when *you* tell SyncForge that data has changed.
- **Built-in Web Application Firewall (WAF)**: Instantly protect your application from SQL Injection, Cross-Site Scripting (XSS), and Path Traversal attacks with a single line of code.
- **Zero-Code Django Auto-Sync**: Use our `@sync_model` decorator to automatically sync data across all your client devices whenever a Django model is created, updated, or deleted.
- **Precision Logging**: The middleware tracks request methods, paths, response statuses, and execution times in milliseconds.
- **Framework Agnostic**: First-class support for Django, FastAPI, Flask, or pure Python scripts.
- **Zero External Dependencies**: Built entirely on the Python Standard Library (`urllib`, `json`, `threading`).

---

## 🚀 Installation

```bash
pip install syncforge
```

---

## 🛡️ Built-in WAF Security Middleware (Django)

SyncForge SDK includes a professional-grade Web Application Firewall (WAF) and request logger. By adding just one line to your `settings.py`, your entire application is instantly protected from hackers.

### Features of the WAF:
1. **SQL Injection Protection**: Blocks `UNION SELECT`, `OR 1=1`, and other common SQLi payloads.
2. **XSS Protection**: Blocks `<script>` tags and malicious javascript injections.
3. **Path Traversal Protection**: Blocks `../` directory traversal attempts.
4. **Security Headers**: Automatically injects strict `X-Content-Type-Options: nosniff`.
5. **Performance Logging**: Logs response times beautifully (e.g., `[GET] /api/ - 200 (12.4ms)`).

### How to Install:
Add it to your Django `MIDDLEWARE` list in `settings.py`:

```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    
    # Add SyncForge Security Firewall right after Django's built-in security
    'syncforge.middleware.SyncForgeSecurityMiddleware',
    
    'django.contrib.sessions.middleware.SessionMiddleware',
    # ...
]
```
*No further configuration needed! Your app is now secure.*

---

## 🔄 The `@sync_model` Decorator (Django Auto-Sync)

If you use Django, you never have to manually trigger a sync again. Use the `@sync_model` decorator. It hooks into Django's `post_save` and `post_delete` signals to automatically broadcast changes to all connected clients.

```python
# models.py
from django.db import models
from syncforge import sf
from syncforge.django import sync_model

# 1. Zero-code Django auto-sync
@sync_model(sf, sync_mode='event')
class Product(models.Model):
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
```
Whenever you call `Product.objects.create(...)` or `product.delete()`, SyncForge automatically invalidates the cache and pushes the new delta to every single user's device.

---

## ⚡ FastAPI & Flask Integration

SyncForge isn't just for Django. You can trigger manual updates from any Python backend.

### FastAPI Example:
```python
import os
from fastapi import FastAPI
from syncforge import SyncForge

# Initialize once
sf = SyncForge(api_key=os.environ.get('SYNCFORGE_API_KEY'))
app = FastAPI()

@app.post("/api/products/")
async def create_product(name: str):
    # 1. Database operation
    db.execute("INSERT INTO products (name) VALUES (?)", (name,))
    
    # 2. Trigger sync — Non-blocking, instant broadcast
    sf.refresh('products')
    
    return {"status": "success"}
```

---

## 📚 Core API Reference

### `SyncForge(api_key, base_url, timeout, silent, async_mode)`

| Parameter    | Default                        | Description                                          |
|--------------|-------------------------------|------------------------------------------------------|
| `api_key`    | required                       | Your API key from the developer dashboard            |
| `base_url`   | `https://syncforge.dev/api`   | Override for local dev / self-hosted                 |
| `timeout`    | `10`                           | HTTP timeout in seconds                              |
| `silent`     | `False`                        | Suppress errors — logs warnings instead of crashing  |
| `async_mode` | `False`                        | Fire-and-forget — refresh runs in a background thread|

### `sf.refresh(*tables)` → `SyncResult | list[SyncResult]`

Broadcasts a refresh signal for the specified tables.

```python
sf.refresh('products')                         # single table
sf.refresh('products', 'categories', 'orders') # multiple at once

result = sf.refresh('products')
print(result.ok)           # True
print(result.calls_saved)  # 1854211 (Analytics data from SyncForge servers)
print(result.sync_mode)    # 'Event — On INSERT / UPDATE / DELETE'
```

### Advanced Usage

```python
# Silent mode — SyncForge errors never crash your app
sf = SyncForge(api_key='sf_live_...', silent=True)

# Async mode — fire-and-forget, returns immediately
sf = SyncForge(api_key='sf_live_...', async_mode=True)
sf.refresh('products')  # returns None, syncs in background
```

---

## 👨‍💻 About the Author

**SyncForge** is passionately built and maintained by **Suresh Dullu Polai**. 
Designed to bring Enterprise-Grade data synchronization and security to developers worldwide, entirely out of the box.

## 📄 License
MIT — see [LICENSE](LICENSE)
