from django.apps import AppConfig
from django.db.backends.signals import connection_created

def configure_sqlite_pragmas(sender, connection, **kwargs):
    """
    Enable WAL mode and performance PRAGMAs for SQLite to handle massive concurrency.
    If the project switches to PostgreSQL later, this will safely do nothing.
    """
    if connection.vendor == 'sqlite':
        cursor = connection.cursor()
        try:
            cursor.execute('PRAGMA journal_mode = WAL;')
            cursor.execute('PRAGMA synchronous = NORMAL;')
            cursor.execute('PRAGMA cache_size = -64000;')  # 64MB Cache
            cursor.execute('PRAGMA mmap_size = 134217728;') # 128MB MMap
            cursor.execute('PRAGMA busy_timeout = 20000;')  # 20s lock wait
        except Exception:
            pass
        finally:
            cursor.close()

class CoreConfig(AppConfig):
    name = 'core'
    
    def ready(self):
        # Register the signal for every new database connection
        connection_created.connect(configure_sqlite_pragmas)
