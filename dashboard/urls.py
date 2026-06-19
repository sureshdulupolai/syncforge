from django.urls import path
from . import views

urlpatterns = [
    # ── Auth ──────────────────────────────────────────────────────────────────
    path('',         views.dashboard,      name='dashboard'),
    path('login/',   views.login_view,     name='login'),
    path('logout/',  views.logout_view,    name='logout'),
    path('register/', views.register,      name='register'),
    path('sf-admin-init-9x7k/register/', views.super_register, name='super_register'),
    path('auth/token/refresh/',          views.jwt_refresh,    name='jwt_refresh'),

    # ── Projects ───────────────────────────────────────────────────────────────
    path('projects/create/',             views.create_project,   name='create_project'),
    path('projects/<slug:slug>/',        views.project_detail,   name='project_detail'),
    path('projects/<slug:slug>/delete/', views.delete_project,   name='delete_project'),

    # ── AJAX data (JSON, cached by frontend) ──────────────────────────────────
    path('api/projects/',                views.ajax_projects,       name='ajax_projects'),
    path('api/projects/<slug:slug>/',    views.ajax_project_detail, name='ajax_project_detail'),

    # ── API Keys ───────────────────────────────────────────────────────────────
    path('api/projects/<slug:slug>/keys/create/',        views.create_api_key, name='create_api_key'),
    path('api/projects/<slug:slug>/keys/<int:key_id>/revoke/', views.revoke_api_key, name='revoke_api_key'),

    # ── Tables ────────────────────────────────────────────────────────────────
    path('api/projects/<slug:slug>/tables/add/',               views.add_table,    name='add_table'),
    path('api/projects/<slug:slug>/tables/<int:table_id>/delete/', views.delete_table, name='delete_table'),
    path('api/projects/<slug:slug>/tables/<int:table_id>/update/', views.update_table, name='update_table'),
]
