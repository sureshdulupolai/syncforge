from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
import uuid, secrets


# ─── Developer Profile ────────────────────────────────────────────────────────

class DeveloperProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    # company_name removed — user can have multiple projects instead

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

def _generate_api_key():
    """Generates a secure key like: sf_live_a1b2c3d4e5f6..."""
    return 'sf_live_' + secrets.token_hex(24)


class APIKey(models.Model):
    project    = models.ForeignKey(Project, on_delete=models.CASCADE,
                                   related_name='api_keys', null=True, blank=True)
    name       = models.CharField(max_length=100, default='Default Key')
    key        = models.CharField(max_length=80, unique=True, default=_generate_api_key)
    is_active  = models.BooleanField(default=True)
    last_used  = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def user(self):
        return self.project.user

    def __str__(self):
        return f'{self.name} ({self.project.name})'


# ─── Table Sync Config ────────────────────────────────────────────────────────

class TableSyncConfig(models.Model):
    SYNC_MODES = [
        ('manual',      'Manual — Refresh Button Only'),
        ('schedule_5m', 'Schedule — Every 5 Minutes'),
        ('schedule_1h', 'Schedule — Every 1 Hour'),
        ('schedule_1d', 'Schedule — Every 1 Day'),
        ('schedule_30d','Schedule — Every 30 Days'),
        ('event',       'Event — On INSERT / UPDATE / DELETE'),
        ('hybrid',      'Hybrid — Event + 24h Verification'),
    ]

    project  = models.ForeignKey(Project, on_delete=models.CASCADE,
                                 related_name='table_configs', null=True, blank=True)
    table_name = models.CharField(max_length=255)
    sync_mode  = models.CharField(max_length=20, choices=SYNC_MODES, default='manual')

    last_sync              = models.DateTimeField(null=True, blank=True)
    next_sync_expected     = models.DateTimeField(null=True, blank=True)
    rows_count             = models.IntegerField(default=0)
    last_change            = models.DateTimeField(null=True, blank=True)
    client_devices         = models.IntegerField(default=0)
    database_calls_saved   = models.BigIntegerField(default=0)
    bandwidth_saved_mb     = models.FloatField(default=0.0)

    class Meta:
        unique_together = ('project', 'table_name')
        ordering = ['table_name']

    @property
    def user(self):
        return self.project.user

    def __str__(self):
        return f'{self.table_name} ({self.get_sync_mode_display()}) — {self.project.name}'
