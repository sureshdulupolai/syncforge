from django.shortcuts import render


def home(request):
    return render(request, 'core/home.html')


def pricing(request):
    return render(request, 'core/pricing.html')


# ── Docs views ────────────────────────────────────────────────────────────────

def docs(request):
    """Docs index — overview and navigation to all sections."""
    return render(request, 'core/docs/index.html')


def docs_getting_started(request):
    return render(request, 'core/docs/getting_started.html')


def docs_django(request):
    return render(request, 'core/docs/django.html')


def docs_flask(request):
    return render(request, 'core/docs/flask.html')


def docs_fastapi(request):
    return render(request, 'core/docs/fastapi.html')


def docs_rest_api(request):
    return render(request, 'core/docs/rest_api.html')


def docs_cache_query(request):
    return render(request, 'core/docs/cache_query.html')


def docs_security(request):
    return render(request, 'core/docs/security.html')


def docs_deployment(request):
    return render(request, 'core/docs/deployment.html')
