# SyncForge Architecture Guide

SyncForge Enterprise is built on a highly modular, strictly decoupled architecture. This document explains the internal mechanisms of the engine, the layers of abstraction, and the end-to-end request lifecycle.

## System Overview Diagram

```text
       [ User Request (API / Web) ]
                  │
                  ▼
       [ Framework Adapter Layer ]
   (Django | FastAPI | Flask | SQLAlchemy)
                  │
                  ▼
          [ Core Engine Layer ]
      (SyncForgeCoreAdapter, Logic)
                  │
          ┌───────┴────────┐
          ▼                ▼
   [ Store Layer ]   [ Event System ]
(InMemory, Redis)    (Telemetry, Logs)
```

## 1. Core Engine Layer (`core.py`)

The **Core Engine** is the centralized brain of the SDK.
- **Responsibility**: It evaluates metadata, manages cache registration (`register_model`), orchestrates invalidation (`trigger_sync`), and governs the background schedulers.
- **Why**: Centralizing the logic prevents code duplication across different web frameworks and ensures identical cache behavior whether you are using Django or a generic Python script.

## 2. Adapter Layer

The **Adapter Layer** provides the framework-specific bindings.
- **Components**: `django.py`, `sqlalchemy.py`, `flask.py`, `fastapi.py`, and `decorators.py`.
- **Responsibility**: These modules listen to their native framework signals (e.g., Django's `post_save` or SQLAlchemy's `after_insert`) and instantly pass the context to the Core Engine.
- **Zero Configuration**: Developers simply apply `@sync_model` or `@sync_function` to their code, and the adapter handles the complex wiring silently.

## 3. Store Layer (`store.py`)

The **Store Layer** abstracts the physical cache persistence.
- **Components**: `InMemoryStore` (default), `DjangoCacheStore`, `RedisStore`.
- **Static Selection**: The backend is selected exactly once during initialization (`sf = SyncForge(backend='in_memory')`).
- **Zero External Dependency**: SyncForge runs perfectly via `InMemoryStore` without requiring Redis, Kafka, or Memcached.
- **Failure Handling Model**: If an external backend like `RedisStore` becomes unreachable, the `StoreManager` automatically triggers a fail-safe mechanism, gracefully degrading to `InMemoryStore` to keep your application alive.

## 4. Event System (`events.py`)

The **Unified Event System** is responsible for non-blocking telemetry and observability.
- **Responsibility**: It dispatches internal tracking events (`CACHE_HIT`, `CACHE_MISS`, `STAMPEDE_LOCK_ACQUIRED`) to the analytics processor without blocking the user's web request.

---

## The Request Flow: How SyncForge Works Internally

### 1. Reading Data (Cache-Aside Lifecycle)
1. **Request Comes In**: A user requests an endpoint (e.g., `GET /products`).
2. **Adapter Triggers Core**: The framework view checks the cache using `sf.get_table()`.
3. **Core Checks Store**: The `StoreManager` looks up the key in the active backend (e.g., `InMemoryStore`).
4. **Cache Hit**: Data is returned instantly (Zero database hits).
5. **Cache Miss**: `sf.cache_query()` executes the database query, serializes the result, and populates the Store Layer.

### 2. Writing Data (Invalidation Lifecycle)
1. **Data Mutated**: A user executes `POST /products` to create a record.
2. **Adapter Hook**: The ORM (`@sync_model`) or function (`@sync_function`) detects the write operation.
3. **Instant Local Invalidation**: The Core Engine immediately instructs the Store Layer to drop the stale payload from RAM.
4. **Background Sync**: The engine spawns a non-blocking daemon thread.
5. **Network Update**: The daemon sends an asynchronous HTTP POST to the SyncForge API, updating metadata globally and alerting other cluster nodes.

---

## Async Safety Model

SyncForge is designed to be fully compatible with asynchronous environments like **FastAPI** and **Starlette**.
- **Non-Blocking Locks**: When mitigating cache stampedes during a cache miss, SyncForge checks for an active `asyncio` event loop. If present, it utilizes a non-blocking lock yield mechanism (`asyncio.sleep()`) instead of blocking the main thread.
- **Request Coalescing**: If 50 users request an expired payload simultaneously, 49 users safely wait on the async lock while exactly 1 user queries the database, perfectly shielding your database from thundering herds.
