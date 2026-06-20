# SyncForge — Single squashed migration
# Represents the complete current model state.
# Generated: 2026-06-20

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import dashboard.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ── DeveloperProfile ──────────────────────────────────────────────────

        migrations.CreateModel(
            name='DeveloperProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),

        # ── Project ───────────────────────────────────────────────────────────

        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('name',        models.CharField(max_length=120)),
                ('slug',        models.SlugField(blank=True, max_length=140, unique=True)),
                ('description', models.TextField(blank=True)),
                ('created_at',  models.DateTimeField(auto_now_add=True)),
                ('updated_at',  models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='projects',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at']},
        ),

        # ── APIKey ────────────────────────────────────────────────────────────

        migrations.CreateModel(
            name='APIKey',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('name',       models.CharField(default='Default Key', max_length=100)),

                # Hashed storage (new, secure)
                ('key_prefix', models.CharField(
                    blank=True, default='', max_length=20,
                    help_text='First 18 chars of the raw key — for dashboard display only.',
                )),
                ('key_hash',   models.CharField(
                    blank=True, null=True, max_length=64, unique=True,
                    help_text='SHA-256 hash of the raw API key.',
                )),

                # Legacy plaintext field — nullable, kept for migration path only
                ('key', models.CharField(
                    blank=True, null=True, max_length=80, unique=True,
                    help_text='DEPRECATED: Legacy plaintext key. Will be removed in a future release.',
                )),

                ('is_active',  models.BooleanField(default=True)),
                ('last_used',  models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),

                ('project', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='api_keys',
                    to='dashboard.project',
                )),
            ],
            options={'ordering': ['-created_at']},
        ),

        # ── TableSyncConfig ───────────────────────────────────────────────────

        migrations.CreateModel(
            name='TableSyncConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('table_name', models.CharField(max_length=255)),
                ('sync_mode',  models.CharField(
                    choices=[
                        ('manual',       'Manual — Refresh Button Only'),
                        ('schedule_5m',  'Schedule — Every 5 Minutes'),
                        ('schedule_1h',  'Schedule — Every 1 Hour'),
                        ('schedule_1d',  'Schedule — Every 1 Day'),
                        ('schedule_30d', 'Schedule — Every 30 Days'),
                        ('event',        'Event — On INSERT / UPDATE / DELETE'),
                        ('hybrid',       'Hybrid — Event + 24h Verification'),
                    ],
                    default='manual', max_length=20,
                )),

                # Timestamps
                ('last_sync',          models.DateTimeField(blank=True, null=True)),
                ('next_sync_expected', models.DateTimeField(blank=True, null=True)),
                ('last_change',        models.DateTimeField(blank=True, null=True)),

                # Versioning
                ('version_number', models.BigIntegerField(
                    default=0,
                    help_text='Incremented on every sf.refresh() call for this table.',
                )),
                ('content_hash', models.CharField(
                    blank=True, default='', max_length=64,
                    help_text='SHA-256 hash of the last serialised dataset.',
                )),

                # Analytics
                ('rows_count',           models.IntegerField(default=0)),
                ('client_devices',       models.IntegerField(default=0)),
                ('database_calls_saved', models.BigIntegerField(default=0)),
                ('bandwidth_saved_mb',   models.FloatField(default=0.0)),

                ('project', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='table_configs',
                    to='dashboard.project',
                )),
            ],
            options={'ordering': ['table_name']},
        ),

        migrations.AlterUniqueTogether(
            name='tablesyncconfig',
            unique_together={('project', 'table_name')},
        ),

        # ── SyncEvent (audit log) ─────────────────────────────────────────────

        migrations.CreateModel(
            name='SyncEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('action', models.CharField(
                    choices=[
                        ('refresh',     'Full Refresh'),
                        ('insert',      'Insert'),
                        ('update',      'Update'),
                        ('delete',      'Delete'),
                        ('bulk_update', 'Bulk Update'),
                        ('bulk_delete', 'Bulk Delete'),
                        ('invalidate',  'Cache Invalidation'),
                    ],
                    default='refresh', max_length=20,
                )),
                ('status', models.CharField(
                    choices=[
                        ('ok',    'Success'),
                        ('error', 'Error'),
                        ('retry', 'Retried'),
                    ],
                    default='ok', max_length=10,
                )),
                ('error_message', models.TextField(blank=True, default='')),
                ('affected_ids',  models.JSONField(blank=True, null=True)),
                ('triggered_at',  models.DateTimeField(auto_now_add=True, db_index=True)),

                ('project', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sync_events',
                    to='dashboard.project',
                )),
                ('table_config', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='events',
                    to='dashboard.tablesyncconfig',
                )),
            ],
            options={'ordering': ['-triggered_at']},
        ),

        migrations.AddIndex(
            model_name='syncevent',
            index=models.Index(
                fields=['project', 'triggered_at'],
                name='sf_syncevent_project_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='syncevent',
            index=models.Index(
                fields=['table_config', 'triggered_at'],
                name='sf_syncevent_table_idx',
            ),
        ),
    ]
