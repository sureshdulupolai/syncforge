from django.urls import path
from . import views

urlpatterns = [
    path("v1/health/",                       views.health,             name="api_health"),
    path("v1/project/",                      views.project_info,       name="api_project_info"),
    path("v1/tables/",                       views.tables_list,        name="api_tables_list"),
    path("v1/sync/<str:table_name>/",        views.smartdb_refresh,    name="smartdb_refresh"),
    # Internal: SDK reports cache hits here (increments database_calls_saved correctly).
    path("v1/cache-hit/<str:table_name>/",   views.cache_hit_report,   name="api_cache_hit"),
]
