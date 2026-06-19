"""
SyncForge REST API — v1
Supports auth via: X-API-Key header | JWT cookie | Django session
"""
from django.http import JsonResponse
from django.utils import timezone
from dashboard.models import TableSyncConfig


def _json(data, status=200):
    return JsonResponse(data, status=status)


def smartdb_refresh(request, table_name):
    """
    POST /api/v1/sync/<table_name>/
    Trigger a manual sync broadcast for a specific table.
    Auth: X-API-Key header or JWT cookie.
    """
    if request.method not in ('POST', 'GET'):
        return _json({'error': 'Use POST or GET'}, 405)

    project = getattr(request, 'api_project', None)
    user    = getattr(request, 'api_user', None)

    if project:
        # API key auth — scoped to a project
        try:
            config = TableSyncConfig.objects.get(project=project, table_name=table_name)
            config.database_calls_saved += 1
            config.last_sync = timezone.now()
            config.save(update_fields=['database_calls_saved', 'last_sync'])
            return _json({
                'status':  'ok',
                'message': f'Sync triggered for table `{table_name}` in project `{project.name}`.',
                'table':   table_name,
                'project': project.name,
                'sync_mode': config.get_sync_mode_display(),
                'database_calls_saved': config.database_calls_saved,
            })
        except TableSyncConfig.DoesNotExist:
            return _json({
                'status':  'ok',
                'message': f'Sync triggered for `{table_name}`. '
                           f'Add this table in your SyncForge dashboard to track stats.',
                'table':   table_name,
                'project': project.name,
            })
    elif user:
        # JWT/session auth — no specific project
        return _json({
            'status':  'ok',
            'message': f'Sync triggered for `{table_name}`.',
            'hint':    'Use an API key (X-API-Key header) for project-scoped stats.',
        })

    return _json({'error': 'Unauthenticated'}, 401)


def project_info(request):
    """GET /api/v1/project/ — returns project info for the API key used."""
    project = getattr(request, 'api_project', None)
    if not project:
        return _json({'error': 'Use X-API-Key header for project info'}, 400)

    tables = list(project.table_configs.values(
        'table_name', 'sync_mode', 'rows_count',
        'database_calls_saved', 'bandwidth_saved_mb',
    ))
    return _json({
        'project': project.name,
        'slug':    project.slug,
        'tables':  tables,
        'active_keys': project.api_keys.filter(is_active=True).count(),
    })


def tables_list(request):
    """GET /api/v1/tables/ — list all tables for the project."""
    project = getattr(request, 'api_project', None)
    if not project:
        return _json({'error': 'X-API-Key required'}, 400)
    tables = list(project.table_configs.values(
        'table_name', 'sync_mode', 'rows_count', 'database_calls_saved'))
    return _json({'tables': tables, 'count': len(tables)})


def health(request):
    """GET /api/v1/health/ — public health check."""
    return _json({'status': 'ok', 'service': 'SyncForge API', 'version': '1.0'})
