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
    path('docs/sqlalchemy/',      views.docs_sqlalchemy,      name='docs_sqlalchemy'),
    path('docs/rest-api/',        views.docs_rest_api,        name='docs_rest_api'),
    path('docs/python-sdk/',      views.docs_python_sdk,      name='docs_python_sdk'),
    path('docs/security/',        views.docs_security,        name='docs_security'),
    path('docs/deployment/',      views.docs_deployment,      name='docs_deployment'),
    path('docs/cmd/',             views.docs_cmd,             name='docs_cmd'),
    
    # Local Docs
    path('docs/local/django/',    views.docs_local_django,    name='docs_local_django'),
    path('docs/local/flask/',     views.docs_local_flask,     name='docs_local_flask'),
    path('docs/local/fastapi/',   views.docs_local_fastapi,   name='docs_local_fastapi'),
    path('docs/local/rest-api/',  views.docs_local_rest_api,  name='docs_local_rest_api'),
]
