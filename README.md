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

SyncForge is the ultimate missing link between simple caching and complex real-time push systems. Built for modern Python frameworks (Django, FastAPI, Flask), it provides an intelligent cache-aside engine with **built-in stampede protection** and a **Web Application Firewall (WAF)**.

Instead of expiring cached data on an arbitrary timer (TTL), SyncForge lets you decide exactly when data becomes stale. 

## 🚀 Why Use SyncForge? (The "0 DB Calls" Advantage)

SyncForge is designed to give your applications **massive speed** and save your database from unnecessary load. 

**Best Use Case:** It is perfect for data that remains static for long periods (e.g., E-Commerce Products, Blog Articles, Global Settings). 
**The Advantage:** If you set a 100-day timer on your database cache, the first user loads the data. For the next 100 days, every subsequent request is served with **0 Database Calls**. The load on your database drops to practically zero, and your application speed multiplies.

### Advanced In-Memory Filtering (Cache as a DB)
Instead of running N+1 queries to filter data (e.g., `category_id=1`), SyncForge allows you to cache the **entire table** once and perform filters instantly in Python RAM. This guarantees 0 database hits across all your filter combinations and eliminates Cache Key Fragmentation.

*(Note: Do not use SyncForge for highly dynamic real-time data like Live Chat, where data changes every second.)*

---

## 🔒 Enterprise-Grade Security & Isolation

### 1. Zero-Data Privacy Architecture
A common misconception is that caching platforms store your users or products on their central servers. **SyncForge does NOT.**
- **What We Sync:** Only lightweight metadata is exchanged (table names, timestamps, HMAC signatures).
- **What Stays Local:** All your actual query results remain strictly inside your own server's RAM or Redis. 

### 2. Multi-Tenant Cache Key Isolation
If multiple clients or projects use SyncForge on the same shared Redis cluster, their data will never mix. The SyncForge SDK automatically hashes your secret `API_KEY` to generate a mathematically unique prefix (e.g., `sf_8a7b9c1d_products`). Client A's data can never overwrite Client B's data.

### 3. Built-in WAF (Web Application Firewall)
The Python SDK includes a production-ready WAF that automatically intercepts malicious payloads.

### 4. Zero-Code Anti-DDoS Rate Limiter 🛡️ [NEW]
Protect your database from cache-busting brute force attacks. Our IP-based Anti-DDoS middleware automatically tracks fetches per IP (even behind proxies like Cloudflare via `HTTP_X_FORWARDED_FOR`).
If an attacker excessively hits the same table, the SDK instantly blocks the IP and returns a `429 Too Many Requests` JSON response.
**Zero code required in your views** — simply enable it on the model:
`@sync_model(sf, sync_mode='event', waf_enabled=True, max_requests=3, block_time_sec=86400)`

## 📂 Project Structure (A-Z)

This repository contains both the Central Server (Dashboard/API) and the SDK.

- **`config/`**: Core Django settings. Fully optimized for high-concurrency SQLite (WAL Mode, Memory-Mapped I/O, 20-second timeout locks).
- **`core/`**: Main frontend templates (`templates/core/`), including the heavily optimized 3-column UI, pricing, and all framework-specific documentation (Django, FastAPI, Flask).
- **`dashboard/`**: The developer portal where you create projects, generate API keys, and track analytics (bandwidth saved, database calls saved).
- **`api/`**: The high-speed REST API endpoints that the SDK communicates with using HMAC-SHA256 signatures.
- **`sdk/`**: The actual `syncforge` Python library installed via `pip`. Contains the framework-agnostic client, Django `@sync_model` decorator, and WAF middleware.

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

# Initialize the client with your unique API key
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
    # 1. First, try to fetch the ENTIRE table from fast memory (0 DB calls!)
    all_products = sf.get_table("core_product")
    
    # 2. On Cache Miss: Hit DB, and save to memory
    if not all_products:
        all_products = list(Product.objects.all())
        # Track this key so @sync_model deletes it when data changes
        sf.track_key("core_product", "sf_myproj_products")
        
    # 3. Filter directly in RAM (Instant speed, 0 DB load)
    active_products = [p for p in all_products if p.is_active]
        
    return render(request, 'list.html', {'products': active_products})
```

---

## 📖 Documentation

Comprehensive guides for Django, FastAPI, Flask, and the REST API are available at:

**[https://syncforge.dev/docs/](https://syncforge.dev/docs/)**

## 📄 License

MIT License. See `LICENSE` for details.
