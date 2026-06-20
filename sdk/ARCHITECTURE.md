# SyncForge — Architecture Reference

> Deep technical documentation for engineers deploying or extending SyncForge.

---

## Core Concept

SyncForge is a **developer-controlled data synchronisation platform**.

Its fundamental contract:

> Serve previously fetched data until the developer explicitly signals that
> the underlying data has changed.

This is different from a cache library (which expires data on a TTL) and
different from a real-time push system (which maintains persistent connections).
SyncForge occupies the space between the two: it is lazy by default
(data is served from memory until told otherwise) but gives the developer
complete, explicit control over when data becomes stale.

---

## System Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Developer's Application                         │
│                                                                      │
│  ┌──────────────┐   sf.refresh()    ┌──────────────────────────┐   │
│  │  Django /    │ ─────────────────▶│    SyncForge SDK         │   │
│  │  FastAPI /   │                   │  (syncforge Python pkg)  │   │
│  │  Flask       │   cache_query()   │                          │   │
│  │  endpoint    │ ◀───────────────▶ │  - HTTP client           │   │
│  └──────────────┘                   │  - Cache wrapper         │   │
│                                     │  - Thread management     │   │
│  ┌──────────────┐                   └─────────┬────────────────┘   │
│  │  Database    │◀──(cache miss)──────────────┘                    │
│  │  (Postgres / │                              │ (HTTP)             │
│  │   SQLite)    │                              ▼                    │
└──┴──────────────┘               ┌──────────────────────────────┐   │
                                  │     SyncForge Server          │   │
                                  │   (syncforge.dev/api)         │   │
                                  │                               │   │
                                  │  - Auth (API Key / JWT)       │   │
                                  │  - Table registry             │   │
                                  │  - Analytics counters         │   │
                                  │  - SyncEvent audit log        │   │
                                  └──────────────────────────────┘   │
                                                                      │
  ┌───────────────────┐                                               │
  │  Django Cache     │◀──────────────────────────────────────────────┘
  │  (Redis/LocMem)   │    (invalidation registry)
  └───────────────────┘
```

---

## Data Flow — Full Lifecycle

### 1. First Request (Cache Miss)

```
HTTP Request arrives
      │
      ▼
sf.cache_query(table_name, cache_key, queryset, timeout)
      │
      ▼
Django cache.get(cache_key) → None (miss)
      │
      ▼
Acquire per-key threading lock  ← stampede protection
      │
      ▼
Double-check cache (another thread may have populated it)
      │
      ▼
list(queryset)  ← DB query executes here
      │
      ▼
cache.set(cache_key, data, timeout)
      │
      ▼
Register cache_key in sf_registry_{table_name}
      │
      ▼
Release lock
      │
      ▼
Return data to caller
```

### 2. Subsequent Requests (Cache Hit)

```
HTTP Request arrives
      │
      ▼
sf.cache_query(table_name, cache_key, ...)
      │
      ▼
Django cache.get(cache_key) → data (HIT)
      │
      ├──▶  Spawn daemon thread → POST /api/v1/cache-hit/{table}/
      │     (increments database_calls_saved atomically — non-blocking)
      │
      ▼
Return data immediately  ← no DB query
```

### 3. Data Change → Invalidation

```
Product.objects.create(...)       or
Product.save()                    or
Product.delete()
      │
      ▼  (Django post_save / post_delete signal)
_trigger_sync() [in calling thread]
      │
      ├──▶ _invalidate_local_cache(table_name)   ← synchronous, fast
      │    cache.delete_many([all registered keys for this table])
      │
      └──▶ Spawn daemon thread → _notify_server()
                │
                ▼
          sf.refresh(table_name)
                │
                ▼
          POST /api/v1/sync/{table_name}/
                │
                ▼
          Server: TableSyncConfig.version_number += 1  (atomic F())
                  SyncEvent.create(action='refresh', status='ok')
```

### 4. Next Request After Invalidation

```
sf.cache_query() → cache MISS (cache was cleared in step 3)
      │
      ▼
list(queryset)  ← fresh DB query
      │
      ▼
cache.set(...)  ← fresh data stored
      │
      ▼
Return fresh data
```

---

## Stampede Protection

**Problem**: When the cache is empty (cold start, after invalidation), many
concurrent requests can all experience a cache miss simultaneously. Without
protection, all of them hit the database at the same time — the "thundering
herd" or "cache stampede" problem.

**Solution**: Per-key threading locks with double-checked locking.

```python
lock = _get_stampede_lock(cache_key)  # one Lock per unique cache_key
with lock:                             # serialise threads here
    data = cache.get(cache_key)        # double-check inside lock
    if data is not None:
        return data                    # another thread already fetched it
    data = list(queryset)              # only ONE thread reaches this
    cache.set(cache_key, data, timeout)
```

**Scope**: This protects threads within a **single process**. For multi-process
protection (Gunicorn with 4 workers), a Redis-based distributed lock would be
needed (not yet implemented — see Roadmap).

**Practical mitigation**: With Redis as the cache backend, only the first
process to encounter the miss queries the database. Other processes' threads
encounter the lock, re-check the cache after the lock is released, and find
the data already populated by the winning process.

---

## Worker Synchronisation

### Problem

Django's default `LocMemCache` stores data in the Python process's heap
memory. With 4 Gunicorn workers:

```
Worker 1  [cache: {products: [A, B, C]}]
Worker 2  [cache: {products: [A, B, C]}]  ← sf.refresh() called here
Worker 3  [cache: {products: [A, B, C]}]    only Worker 2's cache is cleared!
Worker 4  [cache: {products: [A, B, C]}]    Workers 1, 3, 4 serve stale data
```

### Solution: Shared Cache Backend

With Redis, all workers share a single cache store:

```
Worker 1  ─┐
Worker 2  ─┤──▶ Redis ──▶ {sf_registry_core_product: {'active_products'}}
Worker 3  ─┤
Worker 4  ─┘

sf.refresh() in Worker 2:
  → cache.delete_many(['active_products'])  → clears from Redis
  → ALL workers now experience a cache miss on next request ✓
```

### Configuration

```python
# settings.py
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ["REDIS_URL"],
    }
}
```

The SDK detects `LocMemCache` and emits a `RuntimeWarning` to help you
catch this misconfiguration early.

---

## Authentication Architecture

### API Key Flow

```
Request: POST /api/v1/sync/products/
  Headers: X-API-Key: sf_live_abc123...
           X-SF-Timestamp: 1750425000
           X-SF-Signature: 3f2a1b...  (HMAC-SHA256)

UnifiedAuthMiddleware:
  1. Validate X-SF-Timestamp within ±5 minutes (replay protection)
  2. Check Django cache for resolved key (60s TTL)
     HIT  → use cached (project_id, user_id)
     MISS → SHA-256 hash the raw key
          → look up APIKey.key_hash (new) or APIKey.key (legacy)
          → cache the result for 60s
          → update last_used asynchronously
  3. Attach request.api_project, request.api_user
```

### API Key Storage

Keys are stored as **SHA-256 hashes** — the raw key is shown once at creation
and never stored:

```
Creation:
  raw_key = "sf_live_" + secrets.token_hex(24)   # shown to developer
  key_hash = sha256(raw_key)                       # stored in DB
  key_prefix = raw_key[:18]                        # shown in dashboard

Lookup:
  incoming_hash = sha256(request.headers["X-API-Key"])
  APIKey.objects.get(key_hash=incoming_hash, is_active=True)
```

If the database is compromised, exposed hashes cannot be reversed to recover
the original API keys.

### HMAC Request Signing

The SDK signs requests with the API key as the HMAC secret:

```
Signing string:
  METHOD\nURL_PATH\nTIMESTAMP\nSHA256(BODY)

Example:
  POST\n/api/v1/sync/products/\n1750425000\ne3b0c44...

Signature:
  HMAC-SHA256(api_key, signing_string) → hex string

Headers sent:
  X-SF-Timestamp: 1750425000
  X-SF-Signature: 3f2a1b8c...
```

The server validates the timestamp (±5 minutes). Full HMAC signature
validation on the server side is on the roadmap (pending hashed-key
migration completion).

---

## Database Counter Correctness

### Problem with naive increments

```python
# ❌ Race condition under concurrent writes
config.database_calls_saved += 1  # Read: 100
config.save()                      # Write: 101
# Another worker read 100 simultaneously → both write 101 → net increment = 1
```

### F() expressions (current implementation)

```python
# ✅ Atomic database-level increment — no race condition
TableSyncConfig.objects.filter(pk=config.pk).update(
    database_calls_saved=F("database_calls_saved") + 1,
    version_number=F("version_number") + 1,
)
```

The database itself performs the addition atomically. No Python-level read
is involved, so concurrent updates cannot lose increments.

---

## Security Model

| Layer | Mechanism | Protects Against |
|---|---|---|
| API Key hashing | SHA-256, never stored plaintext | DB compromise leaking keys |
| Replay protection | X-SF-Timestamp ±5 min window | Captured request replay |
| API key caching | 60s cache with HMAC-derived cache key | Per-request DB reads |
| Rate limiting | 60 req/min per project (sliding window) | DoS, runaway clients |
| WAF | Regex patterns on URL + POST body | SQLi, XSS, path traversal |
| Security headers | CSP, HSTS, X-Frame-Options, etc. | Browser-based attacks |
| JWT separation | Separate `JWT_SECRET_KEY` from `SECRET_KEY` | Key rotation isolation |
| Input validation | Regex allowlist on table names | Injection via table names |

### What the security model does NOT cover

* Sophisticated, obfuscated injection payloads (use a dedicated WAF / API gateway).
* Application-level authorisation (what data a user is allowed to see).
* Encryption at rest (use database-level or disk-level encryption).
* DDoS at network layer (use a CDN or cloud WAF).

---

## Versioning Model

Every `TableSyncConfig` maintains a `version_number` — a monotonically
increasing integer incremented on every `sf.refresh()` call.

Future use cases:

* **Client version comparison**: clients can send their current version;
  the server returns `304 Not Modified` equivalent if unchanged.
* **Delta sync**: clients with version N can request only records changed
  since version N (not yet implemented — see Roadmap).
* **Audit**: `SyncEvent` records link to the table version at the time
  of each sync event.

---

## Audit Log (SyncEvent)

Every sync signal received by the server creates a `SyncEvent` record:

```python
SyncEvent(
    project=project,
    table_config=config,
    action="refresh",     # or "insert", "update", "delete", etc.
    status="ok",          # or "error"
    error_message="",
    affected_ids=None,    # future: specific row IDs for delta sync
    triggered_at=now(),
)
```

`SyncEvent` is immutable (append-only) and indexed on `(project, triggered_at)`
and `(table_config, triggered_at)` for efficient dashboard timeline queries.

---

## Roadmap

| Feature | Status | Notes |
|---|---|---|
| HMAC server-side validation | Planned | Pending hashed-key migration completion |
| Redis distributed lock (stampede) | Planned | Cross-process stampede protection |
| Delta sync (partial refresh) | Planned | `sf.refresh(table, ids=[1,2,3])` |
| SSE / WebSocket client push | Planned | Server-Sent Events for real-time clients |
| Multi-server sync | Planned | Invalidation across load-balanced servers |
| Legacy `key` field removal | Planned | After all keys migrated to `key_hash` |
| `content_hash` 304 responses | Planned | Skip response body if data unchanged |

---

## File Map

```
sdk/syncforge/
├── __init__.py      — Public exports
├── client.py        — SyncForge class (refresh, cache_query, HTTP)
├── django.py        — @sync_model decorator, sync_migrations
├── middleware.py    — SyncForgeSecurityMiddleware (WAF + headers)
├── exceptions.py    — Exception hierarchy
└── result.py        — SyncResult dataclass

Server (syncforge.dev):
├── api/
│   ├── middleware.py  — UnifiedAuthMiddleware (API Key + JWT + Session)
│   ├── views.py       — REST endpoints (sync, tables, cache-hit, health)
│   ├── rate_limit.py  — Sliding-window rate limiter
│   └── urls.py
├── dashboard/
│   ├── models.py      — Project, APIKey, TableSyncConfig, SyncEvent
│   ├── views.py       — Auth, project/table/key CRUD
│   ├── jwt_utils.py   — Token generation and validation
│   └── migrations/
│       ├── 0001_initial.py
│       ├── 0002_alter_...
│       └── 0003_apikey_hashing_versioning_syncevent.py
└── config/
    ├── settings.py    — Environment-variable-driven configuration
    └── .env.example   — Variable template
```
