<div align="center">
  <img src="static/logo_icon.png" alt="SyncForge Logo" width="120" />
  <h1>SyncForge Enterprise</h1>
  <p><b>The Universal, Framework-Agnostic Intelligent Caching Engine for Python</b></p>
  <p>
    <a href="https://syncforge.dev/docs/">Documentation</a> •
    <a href="https://syncforge.dev/dashboard/">Dashboard</a>
  </p>

  ![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
  ![License](https://img.shields.io/badge/license-MIT-green)
  ![Architecture](https://img.shields.io/badge/architecture-Zero%20Dependency-orange)
</div>

---

SyncForge Enterprise is a high-performance, framework-agnostic cache synchronization layer for Python backends. It completely eradicates redundant database queries and latency spikes by managing an intelligent memory state across your application cluster—**with absolutely zero required external infrastructure.**

No Redis, Kafka, or RabbitMQ clusters needed. SyncForge runs entirely natively in Python memory (`InMemoryStore`), automatically coalescing requests, mitigating cache stampedes, and syncing data across distributed nodes effortlessly.

## 🚀 Why SyncForge Exists

Modern applications often struggle with database bottlenecks. Implementing cache-aside architectures usually requires deploying brittle Redis clusters, maintaining complex invalidation scripts, and duplicating logic across Django, Flask, or FastAPI.

SyncForge replaces this complexity with a single SDK. You fetch data, we handle the stampede locks, memory deduplication, and cross-cluster invalidation safely.

## 🧠 Key Features

- **Zero Infrastructure Needed**: Uses a high-performance `InMemoryStore` by default. No Redis required (though `RedisStore` is optionally available for multi-worker Gunicorn deployments).
- **Universal Framework Adapters**: Identical behavior and API design across **Django**, **FastAPI**, **Flask**, **SQLAlchemy**, and pure Python routes.
- **Cache Stampede Protection**: Async-safe request coalescing dynamically groups identical rapid requests into a single database hit.
- **Unified Event Telemetry**: Integrated non-blocking telemetry tracks `CACHE_HIT`, `CACHE_MISS`, and coalescing efficiency without sending noisy REST API payloads.
- **Stale-While-Revalidate**: Instant lock-yielding ensures secondary threads instantly read stale RAM instead of waiting on P99 database block queues.

## 📐 Architecture Overview

SyncForge features a strictly decoupled, highly reliable architecture:

1. **Core Engine**: The centralized brain (`SyncForgeCoreAdapter`) that evaluates metadata, governs background schedulers, and manages internal telemetry.
2. **Adapter Layer**: Framework-specific bindings (`@sync_model`, `@sync_function`) that seamlessly listen to your ORM's native save/delete signals.
3. **Store Layer**: The interchangeable backend interface (`InMemoryStore`, `DjangoCacheStore`, `RedisStore`). Backends are selected once at initialization, ensuring absolute fail-safe operations (automatic fallback to RAM).

## 💻 Installation

```bash
pip install syncforge
```

## ⚡ Quick Start (FastAPI Example)

SyncForge acts identically across all frameworks. Here is a generic API integration:

### 1. Initialize the Engine
```python
import os
from syncforge import SyncForge

# Initialize ONCE. Zero external dependencies required.
sf = SyncForge(
    api_key=os.environ.get('SYNCFORGE_API_KEY'),
    backend='in_memory',  # Statically select your backend
    async_mode=True
)
```

### 2. Register Your Operations
```python
from syncforge.decorators import sync_function

# SyncForge natively understands when this function is called
@sync_function(sf, table_name="users")
async def update_user(user_id: int, data: dict):
    await db.execute("UPDATE users SET ...")
    # sf.refresh() is automatically triggered in the background!
```

### 3. Smart Cache-Aside Reading
```python
@app.get("/users")
async def list_users():
    # 1. Check ultra-fast RAM cache
    users = sf.get_table("users")
    
    if not users:
        # 2. On miss: fetch DB, SyncForge handles the locking & storage
        users = sf.cache_query(
            table_name="users",
            queryset=await fetch_users_from_db(),
            # cache_key is optional. If omitted, SyncForge securely auto-generates 
            # a unique key by hashing your exact query/queryset to prevent data collisions.
        )
    return {"users": users}
```

## 🔄 Real Usage Flow: How It Works Internally

1. **Request Arrives**: A user fetches `/users`.
2. **Cache Check**: `sf.get_table()` checks the `StoreLayer`.
3. **Cache Miss**: `sf.cache_query()` is triggered. SyncForge safely acquires an async-safe lock, executes the DB fetch, and populates the `InMemoryStore`.
4. **Data Mutation**: A user makes a `POST /users` request. The adapter (`@sync_model` or `@sync_function`) detects the change.
5. **Instant Local Invalidation**: The Core Engine instantly drops the stale `InMemoryStore` data.
6. **Background Sync**: A non-blocking thread notifies the SyncForge server to update metadata globally across your cluster.

## 📈 Performance Benefits

- **Database Reads**: Reduces repetitive DB I/O by >95% for read-heavy routes.
- **Latency**: P50 read times drop to <1ms via pure RAM memory access.
- **Infrastructure Cost**: Eliminates mandatory Redis/ElastiCache clusters for caching arrays.

## 🌍 Environment Parity: Live vs. Local Dev Mode

SyncForge ensures **100% environment parity** for your developers. By initializing the client with `dev_mode=True`, the SDK flawlessly simulates live API behavior without ever hitting the SyncForge servers or a live database.

- **Zero Load Development**: When `@sync_model` is executed locally, it automatically creates a fully structured Mock Cache Table in your local RAM/Disk registry.
- **Professional API Responses**: Features like `create_table` and `get_table` will return professional API dictionaries (e.g. `{"success": True, "status": "ok"}`) that perfectly mirror the live cluster responses.
- **Local Utilities**: 
  - `sf.all_table()`: Returns a list of all tables registered in your current environment (Live or Local).
  - `sf.filter_table(names)`: Quickly verifies the existence of tables, returning a smart `Dict[str, bool]` mapping.
  - `sf.clear_local_table()`: Wipes the local cache and table registry, cleanly resetting your local state. (Subsequent `get_table` calls will accurately return `not_found`).

These tools guarantee that testing your backend caching logic locally behaves exactly identical to your production deployment!

## 🛡️ Production Readiness

SyncForge is enterprise-grade out of the box:
- **Fail-Safe Mechanism**: If an optional `RedisStore` goes offline, the SDK instantly falls back to `InMemoryStore` without crashing your application.
- **Silent Mode**: `silent=True` ensures the caching layer never raises 500 exceptions into your main application lifecycle.
- **Zero-Data Privacy**: SyncForge servers NEVER see your database rows. We only synchronize timestamps, metadata, and cache keys. Your PII stays local.

---
## 📖 Documentation & Support

Comprehensive guides for Django, FastAPI, Flask, and SQLAlchemy are available at:

**[https://syncforge.dev/docs/](https://syncforge.dev/docs/)**
