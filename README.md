<div align="center">
  <img src="static/logo_icon.png" alt="SyncForge Logo" width="120" />
  <h1>SyncForge</h1>
  <p><b>The Developer-Controlled Smart Data Synchronization Platform</b></p>
  <p>
    <a href="https://syncforge.dev/docs/">Documentation</a> •
    <a href="https://syncforge.dev/dashboard/">Dashboard</a> •
    <a href="#security--zero-data-privacy">Security</a>
  </p>
</div>

---

SyncForge is the missing link between simple caching and complex real-time push systems. It provides an intelligent cache-aside engine with **built-in stampede protection**, powered by explicit developer-triggered invalidation.

Instead of expiring cached data on an arbitrary timer (TTL), SyncForge lets you decide exactly when data becomes stale.

## 🚀 The Core Concept

1. **Read**: `sf.get_table()` instantly serves queries from your fast local memory.
2. **Write**: You call `sf.refresh('table_name')` after writing to your database.
3. **Invalidate**: SyncForge instantly clears the local cache across all your workers and notifies the central dashboard.

**The result?** Zero polling, zero unnecessary database queries, and your clients always get fresh data exactly when it changes.

---

## 🔒 Security & Zero-Data Privacy Architecture

A common misconception is that caching platforms store your users, products, or financial records on their central servers. **SyncForge does NOT.**

SyncForge operates on a strict **zero-data model**:
- **What We Sync:** Only lightweight synchronization metadata is exchanged over the network (table identifiers, invalidation timestamps, cache keys, and HMAC-signed API credentials).
- **What Stays Local:** All your actual query results and application data remain strictly inside your own infrastructure.

### Enterprise-Grade Security Features
- **HMAC-SHA256 Request Signing**: Every API call is cryptographically signed and timestamped, making replay attacks and payload forgery mathematically impossible.
- **Cache Stampede Protection**: Double-checked threading locks ensure that even if 10,000 users hit an expired cache at the exact same millisecond, only exactly **1** database query is executed.
- **Built-in WAF**: The Python SDK includes a Web Application Firewall that blocks URL-encoded XSS, SQLi, and Path Traversal attacks before they even reach your views.

---

## 💻 Installation

```bash
pip install syncforge
```

## ⚡ Quick Start

### 1. Initialize

```python
import os
from syncforge import SyncForge

sf = SyncForge(api_key=os.environ['SYNCFORGE_API_KEY'])
```

### 2. Auto-sync your models (Django Example)

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

### 3. Read Data (Framework Agnostic)

SyncForge natively supports Django's cache framework, but automatically falls back to an internal, highly-concurrent `InMemoryCache` for **FastAPI, Flask, and plain Python**.

```python
# views.py (or FastAPI router)
from myproject.sf import sf
from .models import Product

def product_list(request):
    # 1. First, try to fetch from fast memory
    if sf.get_table("core_product"):
        products = sf.get_table("core_product")
    else:
        # 2. On Cache Miss: Hit DB, and automatically save to memory
        products = sf.cache_query(
            table_name='core_product',
            queryset=Product.objects.filter(active=True)
            # cache_key omitted! Auto-generates as 'sf_auto_core_product'
        )
        
    return render(request, 'list.html', {'products': products})
```

---

## 🛡️ Web Application Firewall (WAF)

SyncForge SDK includes a production-ready Web Application Firewall. Protect your app with one line.

```python
# Django settings.py
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'syncforge.middleware.SyncForgeSecurityMiddleware',  # Add this line
    # ...
]
```

It automatically intercepts:
* **SQL Injection (SQLi):** `UNION SELECT`, `OR 1=1`, `DROP TABLE`
* **Cross-Site Scripting (XSS):** `<script>`, `javascript:`, `onerror=`
* **Path Traversal:** `../`, `/etc/passwd`
* **Security Headers:** Automatically injects `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and CSP headers.

---

## 📖 Documentation

Comprehensive guides for Django, FastAPI, Flask, and the REST API are available at:

**[https://syncforge.dev/docs/](https://syncforge.dev/docs/)**

## 📄 License

MIT License. See `LICENSE` for details.
