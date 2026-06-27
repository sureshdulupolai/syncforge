from django.shortcuts import render, redirect


def home(request):
    return render(request, 'core/home.html')


def pricing(request):
    return render(request, 'core/pricing.html')


def custom_404(request, exception=None):
    """
    Custom 404 error handler that renders the professional 404.html template.
    """
    return render(request, '404.html', status=404)


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


def docs_sqlalchemy(request):
    return render(request, 'core/docs/sqlalchemy.html')


def docs_rest_api(request):
    return render(request, 'core/docs/rest_api.html')


def docs_python_sdk(request):
    return render(request, 'core/docs/python_sdk.html')


def docs_security(request):
    return render(request, 'core/docs/security.html')


def docs_deployment(request):
    return render(request, 'core/docs/deployment.html')


def docs_cmd(request):
    return render(request, 'core/docs/cmd.html')

# -- Local Docs Views --

def docs_local_django(request):
    return render(request, 'core/docs/local_django.html')

def docs_local_flask(request):
    return render(request, 'core/docs/local_flask.html')

def docs_local_fastapi(request):
    return render(request, 'core/docs/local_fastapi.html')

def docs_local_rest_api(request):
    return render(request, 'core/docs/local_rest_api.html')
