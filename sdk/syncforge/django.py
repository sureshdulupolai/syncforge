"""
Django integration for SyncForge.
Provides the @sync_model decorator to auto-sync Django models.
"""
import logging

try:
    from django.db.models.signals import post_save, post_delete
    from django.apps import apps
    HAS_DJANGO = True
except ImportError:
    HAS_DJANGO = False

logger = logging.getLogger("syncforge")

_registered_tables = set()

def sync_model(sf_client, sync_mode='event'):
    """
    Class decorator for Django models to automatically sync with SyncForge.
    
    Example:
        from syncforge import sf
        from syncforge.django import sync_model
        
        @sync_model(sf)
        class Product(models.Model):
            name = models.CharField(max_length=100)
    """
    def decorator(cls):
        if not HAS_DJANGO:
            raise ImportError("Django is not installed. Cannot use @sync_model.")
            
        table_name = cls._meta.db_table
        
        # 1. Register table on SyncForge dashboard
        try:
            sf_client.create_table(table_name, sync_mode=sync_mode)
        except Exception as e:
            logger.warning(f"[SyncForge] Failed to register table {table_name}: {e}")
            
        _registered_tables.add(table_name)
            
        # 2. Hook into ORM signals to trigger syncs automatically
        def _trigger_sync(sender, **kwargs):
            try:
                # -----------------
                # Smart Cache Invalidation
                # -----------------
                try:
                    from django.core.cache import cache
                    registry_key = f"sf_registry_{table_name}"
                    keys = cache.get(registry_key, set())
                    if keys:
                        cache.delete_many(keys)
                        cache.delete(registry_key)
                except Exception as e:
                    logger.warning(f"[SyncForge] Cache invalidation failed: {e}")
                
                # -----------------
                # Broadcast to SyncForge Server
                # -----------------
                sf_client.refresh(table_name)
            except Exception as e:
                logger.error(f"[SyncForge] Failed to trigger sync for {table_name}: {e}")
                
        # Connect signals
        post_save.connect(_trigger_sync, sender=cls, weak=False, dispatch_uid=f"sf_save_{table_name}")
        post_delete.connect(_trigger_sync, sender=cls, weak=False, dispatch_uid=f"sf_delete_{table_name}")
        
        return cls
    return decorator


def sync_migrations(sf_client):
    """
    Removes tables from the SyncForge dashboard that no longer exist in your Django project.
    Call this inside an AppConfig.ready() or after your migrations run.
    """
    if not HAS_DJANGO:
        return
        
    try:
        active_tables = {model._meta.db_table for model in apps.get_models()}
        
        # Fetch current registered tables from SyncForge
        sf_tables = sf_client.list_tables()
        for t in sf_tables:
            t_name = t.get('table_name')
            if t_name and t_name not in active_tables:
                sf_client.delete_table(t_name)
                logger.info(f"[SyncForge] Cleaned up deleted table: {t_name}")
    except Exception as e:
        logger.warning(f"[SyncForge] sync_migrations cleanup failed: {e}")
