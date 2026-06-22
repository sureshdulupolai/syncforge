from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
import uuid, secrets, hashlib


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _generate_raw_api_key() -> str:
    """Generate a cryptographically secure API key: sf_live_<48 hex chars>."""
    return 'sf_live_' + secrets.token_hex(24)


def hash_api_key(raw_key: str) -> str:
    """
    Return the SHA-256 hash of a raw API key.
    Keys are stored hashed — the plaintext is shown once at creation and never stored.
    """
    return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()


# ─── Developer Profile ────────────────────────────────────────────────────────

class DeveloperProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.user.username


# ─── Project ──────────────────────────────────────────────────────────────────

class Project(models.Model):
    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
    name        = models.CharField(max_length=120)
    slug        = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug = base
            n = 1
            while Project.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.user.username} / {self.name}'


# ─── API Key ──────────────────────────────────────────────────────────────────

class APIKey(models.Model):
    """
    API keys are stored as SHA-256 hashes — never as plaintext.

    The raw key is generated once and returned to the developer at creation.
    It is never stored in the database.

    ``key_prefix`` stores the first 18 characters (e.g. ``sf_live_a1b2c3d4``)
    for display purposes in the dashboard without exposing the full key.

    Migration path: the legacy ``key`` field is kept temporarily to allow
    zero-downtime migration. It will be removed in a future release after
    all existing keys have been re-hashed.
    """
    project    = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name='api_keys', null=True, blank=True,
    )
    name       = models.CharField(max_length=100, default='Default Key')

    # ── Hashed storage (new, secure) ──────────────────────────────────────────
    key_prefix = models.CharField(
        max_length=20, blank=True,
        help_text='First 18 chars of the raw key — displayed in dashboard only.',
    )
    key_hash   = models.CharField(
        max_length=64, unique=True, null=True, blank=True,
        help_text='SHA-256 hash of the raw API key.',
    )

    # ── Legacy plaintext field (DEPRECATED — kept for migration only) ─────────
    # Will be removed after all keys are migrated to key_hash.
    key        = models.CharField(
        max_length=80, unique=True, null=True, blank=True,
        help_text='DEPRECATED: Legacy plaintext key. Do not use for new keys.',
    )

    is_active  = models.BooleanField(default=True)
    last_used  = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @classmethod
    def create_new(cls, project, name='Default Key') -> tuple['APIKey', str]:
        """
        Create a new API key. Returns (APIKey instance, raw_key_string).
        The raw key is shown once — store it securely.

        Usage::

            api_key_obj, raw_key = APIKey.create_new(project, name='Production Key')
            # Show raw_key to the user ONCE — it cannot be recovered later.
        """
        raw    = _generate_raw_api_key()
        hashed = hash_api_key(raw)
        prefix = raw[:18]
        obj    = cls.objects.create(
            project=project,
            name=name,
            key_prefix=prefix,
            key_hash=hashed,
        )
        return obj, raw

    @property
    def user(self):
        return self.project.user

    def __str__(self):
        return f'{self.name} ({self.project.name if self.project else "no project"})'


# ─── Table Sync Config ────────────────────────────────────────────────────────

class TableSyncConfig(models.Model):
    SYNC_MODES = [
        ('manual',       'Manual — Refresh Button Only'),
        ('schedule_5m',  'Schedule — Every 5 Minutes'),
        ('schedule_1h',  'Schedule — Every 1 Hour'),
        ('schedule_1d',  'Schedule — Every 1 Day'),
        ('schedule_30d', 'Schedule — Every 30 Days'),
        ('event',        'Event — On INSERT / UPDATE / DELETE'),
        ('hybrid',       'Hybrid — Event + 24h Verification'),
    ]

    project    = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name='table_configs', null=True, blank=True,
    )
    table_name = models.CharField(max_length=255)
    sync_mode  = models.CharField(max_length=20, choices=SYNC_MODES, default='manual')

    # ── Timestamps ────────────────────────────────────────────────────────────
    last_sync          = models.DateTimeField(null=True, blank=True)
    next_sync_expected = models.DateTimeField(null=True, blank=True)
    last_change        = models.DateTimeField(null=True, blank=True)

    # ── Enterprise Cache Config ───────────────────────────────────────────────
    active           = models.BooleanField(default=True)
    STORAGE_MODES = [
        ('ram_only', 'RAM Only'),
        ('ram_disk', 'RAM + Disk Persistent'),
        ('disabled', 'Disabled (Direct DB)'),
    ]
    storage_mode     = models.CharField(max_length=20, choices=STORAGE_MODES, default='ram_disk')
    
    COMPRESSION_TYPES = [
        ('none', 'No Compression'),
        ('lz4', 'LZ4 (Fast)'),
        ('zstd', 'Zstandard (High Ratio)'),
        ('gzip', 'GZIP'),
    ]
    compression      = models.CharField(max_length=10, choices=COMPRESSION_TYPES, default='none')
    encryption       = models.BooleanField(default=False)
    
    PRIORITIES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    priority         = models.CharField(max_length=10, choices=PRIORITIES, default='medium')
    refresh_interval = models.IntegerField(default=0, help_text="Refresh interval in minutes (0 = event only)")

    # ── Versioning ────────────────────────────────────────────────────────────
    # Monotonically increasing version number — incremented on every refresh.
    # Clients can compare their version to detect staleness.
    cache_version  = models.BigIntegerField(default=1)

    # SHA-256 hash of the last serialised dataset.
    # Allows the server to return 304-like "not modified" responses.
    content_hash   = models.CharField(max_length=64, blank=True, default='')

    # ── Analytics ─────────────────────────────────────────────────────────────
    rows_count           = models.IntegerField(default=0)
    client_devices       = models.IntegerField(default=0)

    # How many times clients received data from cache instead of hitting DB.
    # Incremented on cache hit, NOT on refresh signal.
    database_calls_saved = models.BigIntegerField(default=0)

    # Estimated bandwidth saved (MB) — updated periodically.
    bandwidth_saved_mb   = models.FloatField(default=0.0)

    # ── Advanced Analytics ────────────────────────────────────────────────────
    cache_hits           = models.BigIntegerField(default=0)
    cache_misses         = models.BigIntegerField(default=0)
    total_requests       = models.BigIntegerField(default=0)
    avg_response_time_ms = models.FloatField(default=0.0)

    class Meta:
        unique_together = ('project', 'table_name')
        ordering        = ['table_name']

    @property
    def user(self):
        return self.project.user

    def __str__(self):
        return f'{self.table_name} ({self.get_sync_mode_display()}) — {self.project.name}'


# ─── Sync Event Audit Log ─────────────────────────────────────────────────────

class SyncEvent(models.Model):
    """
    Immutable audit log of every sync signal received by the server.

    Records who triggered the sync, which table, what action, and whether
    it succeeded. Used for the dashboard timeline view and debugging.
    """
    ACTION_CHOICES = [
        ('refresh',     'Full Refresh'),
        ('insert',      'Insert'),
        ('update',      'Update'),
        ('delete',      'Delete'),
        ('bulk_update', 'Bulk Update'),
        ('bulk_delete', 'Bulk Delete'),
        ('invalidate',  'Cache Invalidation'),
    ]

    STATUS_CHOICES = [
        ('ok',    'Success'),
        ('error', 'Error'),
        ('retry', 'Retried'),
    ]

    table_config  = models.ForeignKey(
        TableSyncConfig, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='events',
    )
    project       = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='sync_events',
    )
    action        = models.CharField(max_length=20, choices=ACTION_CHOICES, default='refresh')
    status        = models.CharField(max_length=10, choices=STATUS_CHOICES, default='ok')
    error_message = models.TextField(blank=True, default='')

    # Optional: which specific row IDs were affected (for delta sync)
    affected_ids  = models.JSONField(null=True, blank=True)

    triggered_at  = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-triggered_at']
        indexes  = [
            models.Index(fields=['project', 'triggered_at']),
            models.Index(fields=['table_config', 'triggered_at']),
        ]

    def __str__(self):
        return f'[{self.status}] {self.action} on {self.table_config} at {self.triggered_at}'
