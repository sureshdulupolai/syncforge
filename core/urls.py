from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('pricing/', views.pricing, name='pricing'),

    # Docs — main index
    path('docs/', views.docs, name='docs'),

    # Docs — individual pages
    path('docs/getting-started/', views.docs_getting_started, name='docs_getting_started'),
    path('docs/django/',          views.docs_django,          name='docs_django'),
    path('docs/flask/',           views.docs_flask,           name='docs_flask'),
    path('docs/fastapi/',         views.docs_fastapi,         name='docs_fastapi'),
    path('docs/rest-api/',        views.docs_rest_api,        name='docs_rest_api'),
    path('docs/cache-query/',     views.docs_cache_query,     name='docs_cache_query'),
    path('docs/security/',        views.docs_security,        name='docs_security'),
    path('docs/deployment/',      views.docs_deployment,      name='docs_deployment'),
]
