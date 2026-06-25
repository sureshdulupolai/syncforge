import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import Project, TableSyncConfig, ProjectLog

@login_required
@require_POST
def cmd_execute(request, slug):
    project = get_object_or_404(Project, slug=slug, user=request.user)
    
    try:
        body = json.loads(request.body)
        command = body.get('command', '').strip()
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
        
    if not command:
        return JsonResponse({'output': ''})
        
    parts = command.split()
    cmd = parts[0].lower()
    
    # CMD: logs
    if cmd == 'logs':
        logs = project.logs.all()[:20]
        if not logs:
            return JsonResponse({'output': 'No recent events found for this project.'})
            
        output_lines = ["--- SyncForge Event Logs ---"]
        for log in logs:
            ts = log.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            event = log.get_event_type_display()
            output_lines.append(f"[{ts}] [{event}] {log.details}")
            
        return JsonResponse({'output': '\n'.join(output_lines)})
        
    # CMD: table <name>
    elif cmd == 'table':
        if len(parts) < 2:
            return JsonResponse({'output': 'Usage: table <table_name>'})
        
        table_name = parts[1]
        try:
            table = project.table_configs.get(table_name=table_name)
        except TableSyncConfig.DoesNotExist:
            return JsonResponse({'output': f'Error: Table "{table_name}" not found in this project.'})
            
        # We fetch metadata as instructed (Zero-Knowledge)
        output_lines = [
            f"--- Fetching Data Profile for '{table_name}' ---",
            f"Note: Actual row data is securely cached in your local environment.",
            f"",
            f"Database Status: {'Active' if table.active else 'Inactive'}",
            f"Current Version: v{table.cache_version}",
            f"Sync Mode:       {table.get_sync_mode_display()}",
            f"Storage Mode:    {table.get_storage_mode_display()}",
            f"Encryption:      {'Enabled (AES-256)' if table.encryption else 'Disabled'}",
            f"Compression:     {table.get_compression_display()}",
            f"Cache Hits:      {table.cache_hits}",
            f"Last Refreshed:  {table.last_sync.strftime('%Y-%m-%d %H:%M:%S') if table.last_sync else 'Never'}"
        ]
        return JsonResponse({'output': '\n'.join(output_lines)})
        
    # CMD: clear
    elif cmd == 'clear':
        return JsonResponse({'output': 'CLEAR_TERMINAL'})
        
    # CMD: help
    elif cmd == 'help':
        output = (
            "Available Commands:\n"
            "  logs              - View recent events (Refresh, Create, API keys)\n"
            "  table <name>      - Fetch secure real-time sync data profile for a table\n"
            "  clear             - Clear the terminal screen\n"
            "  help              - Show this message"
        )
        return JsonResponse({'output': output})
        
    else:
        return JsonResponse({'output': f'Command not found: {cmd}. Type "help" for a list of commands.'})
