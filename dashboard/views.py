from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, authenticate
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.utils import timezone
import re, json

from .models import DeveloperProfile, Project, APIKey, TableSyncConfig
from .jwt_utils import (generate_access_token, generate_refresh_token,
                        decode_token, is_token_expiring_soon)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _set_jwt(response, user):
    access  = generate_access_token(user)
    refresh = generate_refresh_token(user)
    opts = dict(httponly=True, samesite='Lax', secure=False)
    response.set_cookie('sf_access_token',  access,
                        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60, **opts)
    response.set_cookie('sf_refresh_token', refresh,
                        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400, **opts)


def _clear_jwt(response):
    response.delete_cookie('sf_access_token')
    response.delete_cookie('sf_refresh_token')


def _pw_valid(pw):
    errors = []
    if len(pw) < 8:            errors.append('At least 8 characters required.')
    if len(pw) > 128:          errors.append('Password cannot exceed 128 characters.')
    if not re.search(r'[A-Z]', pw): errors.append('At least one uppercase letter (A–Z).')
    if not re.search(r'[a-z]', pw): errors.append('At least one lowercase letter (a–z).')
    if not re.search(r'\d',    pw): errors.append('At least one number (0–9).')
    if not re.search(r'[!@#$%^&*()\-_=+\[\]{}|;:,.<>?/`~\'"@\\]', pw):
        errors.append('At least one special character (!@#$…).')
    return not errors, errors


def _json(data, status=200):
    return JsonResponse(data, status=status)


# ─── Auth ─────────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    errors, form_data = {}, {}

    if request.method == 'POST':
        form_data['email'] = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')

        if not form_data['email']:
            errors['email'] = 'Email is required.'
        elif not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', form_data['email']):
            errors['email'] = 'Enter a valid email address.'
        if not password:
            errors['password'] = 'Password is required.'

        if not errors:
            from django.contrib.auth.models import User
            try:
                user_obj = User.objects.get(email__iexact=form_data['email'])
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                user = None
            if user:
                login(request, user)
                response = redirect('dashboard')
                _set_jwt(response, user)
                return response
            errors['general'] = 'Invalid email or password.'

    return render(request, 'dashboard/login.html', {'errors': errors, 'form_data': form_data})


def logout_view(request):
    logout(request)
    response = redirect('home')
    _clear_jwt(response)
    return response


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    errors, form_data = {}, {}

    if request.method == 'POST':
        form_data = {
            'email':     request.POST.get('email', '').strip().lower(),
            'password1': request.POST.get('password1', ''),
            'password2': request.POST.get('password2', ''),
        }

        # Email validation
        email = form_data['email']
        if not email:
            errors['email'] = 'Email is required.'
        elif not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            errors['email'] = 'Enter a valid email address.'
        elif len(email) > 254:
            errors['email'] = 'Email cannot exceed 254 characters.'

        # Password validation
        ok, pw_errs = _pw_valid(form_data['password1'])
        if not ok:
            errors['password1'] = pw_errs[0]
        elif form_data['password1'] != form_data['password2']:
            errors['password2'] = 'Passwords do not match.'

        if not errors:
            from django.contrib.auth.models import User
            if User.objects.filter(email__iexact=email).exists():
                errors['email'] = 'An account with this email already exists.'
            else:
                # Auto-generate a unique internal username from email prefix
                base = re.sub(r'[^a-zA-Z0-9_]', '_', email.split('@')[0])[:20]
                username = base
                suffix = 1
                while User.objects.filter(username=username).exists():
                    username = f'{base}_{suffix}'
                    suffix += 1

                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=form_data['password1'],
                )
                DeveloperProfile.objects.create(user=user)
                login(request, user)
                response = redirect('dashboard')
                _set_jwt(response, user)
                return response

    return render(request, 'dashboard/register.html', {'errors': errors, 'form_data': form_data})


def super_register(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect('dashboard')
    errors = {}
    gate_passed = request.session.get('super_gate_passed', False)

    if request.method == 'POST' and not gate_passed:
        if request.POST.get('access_code') == settings.SUPER_REGISTER_PASSWORD:
            request.session['super_gate_passed'] = True
            gate_passed = True
        else:
            errors['access_code'] = 'Incorrect access code.'

    if request.method == 'POST' and gate_passed and 'access_code' not in request.POST:
        form_data = {
            'username':  request.POST.get('username', '').strip(),
            'email':     request.POST.get('email', '').strip(),
            'password1': request.POST.get('password1', ''),
            'password2': request.POST.get('password2', ''),
        }
        ok, pw_errs = _pw_valid(form_data['password1'])
        if not form_data['username']:
            errors['username'] = 'Required.'
        if not ok:
            errors['password1'] = pw_errs[0]
        elif form_data['password1'] != form_data['password2']:
            errors['password2'] = 'Passwords do not match.'
        if not errors:
            from django.contrib.auth.models import User
            if User.objects.filter(username=form_data['username']).exists():
                errors['username'] = 'Already taken.'
            else:
                user = User.objects.create_superuser(
                    username=form_data['username'], email=form_data['email'],
                    password=form_data['password1'])
                DeveloperProfile.objects.get_or_create(user=user)
                request.session.pop('super_gate_passed', None)
                login(request, user)
                response = redirect('dashboard')
                _set_jwt(response, user)
                return response
        return render(request, 'dashboard/super_register.html',
                      {'gate_passed': True, 'errors': errors, 'form_data': form_data})

    return render(request, 'dashboard/super_register.html',
                  {'gate_passed': gate_passed, 'errors': errors})


# ─── JWT Refresh ──────────────────────────────────────────────────────────────

@require_POST
def jwt_refresh(request):
    token = request.COOKIES.get('sf_refresh_token')
    if not token:
        return _json({'error': 'No refresh token'}, 401)
    try:
        payload = decode_token(token)
        if payload.get('type') != 'refresh':
            raise ValueError
        from django.contrib.auth.models import User
        user = User.objects.get(pk=payload['user_id'])
        new_access = generate_access_token(user)
        resp = _json({'status': 'refreshed'})
        resp.set_cookie('sf_access_token', new_access,
                        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                        httponly=True, samesite='Lax', secure=False)
        return resp
    except Exception as e:
        return _json({'error': str(e)}, 401)


# ─── Dashboard (project list) ─────────────────────────────────────────────────

@login_required
def dashboard(request):
    projects = Project.objects.filter(user=request.user).prefetch_related('api_keys', 'table_configs')
    return render(request, 'dashboard/dashboard.html', {'projects': projects})


# ─── Project CRUD ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def create_project(request):
    name = request.POST.get('name', '').strip()
    desc = request.POST.get('description', '').strip()
    if not name:
        return _json({'error': 'Project name is required.'}, 400)
    project = Project.objects.create(user=request.user, name=name, description=desc)
    # Auto-create first API key
    key = APIKey.objects.create(project=project, name='Default Key')
    return _json({
        'status': 'created',
        'project': {'id': project.id, 'name': project.name, 'slug': project.slug},
        'api_key': key.key,
    })


@login_required
def project_detail(request, slug):
    project = get_object_or_404(Project, slug=slug, user=request.user)
    tables  = project.table_configs.all()
    keys    = project.api_keys.filter(is_active=True)
    return render(request, 'dashboard/project_detail.html', {
        'project': project,
        'tables':  tables,
        'api_keys': keys,
    })


@login_required
@require_POST
def delete_project(request, slug):
    project = get_object_or_404(Project, slug=slug, user=request.user)
    project.delete()
    return redirect('dashboard')


# ─── AJAX data endpoints (cached by frontend) ─────────────────────────────────

@login_required
def ajax_projects(request):
    """Returns all projects as JSON — frontend caches in localStorage."""
    projects = Project.objects.filter(user=request.user).prefetch_related('api_keys', 'table_configs')
    data = []
    for p in projects:
        data.append({
            'id':           p.id,
            'name':         p.name,
            'slug':         p.slug,
            'description':  p.description,
            'created_at':   p.created_at.isoformat(),
            'api_key_count': p.api_keys.filter(is_active=True).count(),
            'table_count':  p.table_configs.count(),
            'calls_saved':  sum(t.database_calls_saved for t in p.table_configs.all()),
        })
    return _json({'projects': data, 'ts': timezone.now().isoformat()})


@login_required
def ajax_project_detail(request, slug):
    """Returns single project data as JSON."""
    project = get_object_or_404(Project, slug=slug, user=request.user)
    tables  = list(project.table_configs.values(
        'id', 'table_name', 'sync_mode', 'rows_count',
        'database_calls_saved', 'bandwidth_saved_mb', 'last_sync',
    ))
    keys    = []
    for k in project.api_keys.filter(is_active=True):
        keys.append({
            'id':         k.id,
            'name':       k.name,
            'key_prefix': k.key[:18] + '...',
            'key_full':   k.key,        # shown only on explicit reveal
            'created_at': k.created_at.isoformat(),
            'last_used':  k.last_used.isoformat() if k.last_used else None,
        })
    return _json({
        'project': {
            'id':          project.id,
            'name':        project.name,
            'slug':        project.slug,
            'description': project.description,
        },
        'tables':   tables,
        'api_keys': keys,
        'ts': timezone.now().isoformat(),
    })


# ─── API Key management ───────────────────────────────────────────────────────

@login_required
@require_POST
def create_api_key(request, slug):
    project = get_object_or_404(Project, slug=slug, user=request.user)
    name = request.POST.get('name', 'New Key').strip() or 'New Key'
    if project.api_keys.filter(is_active=True).count() >= 5:
        return _json({'error': 'Maximum 5 active API keys per project.'}, 400)
    key = APIKey.objects.create(project=project, name=name)
    return _json({'status': 'created', 'key': key.key, 'id': key.id, 'name': key.name})


@login_required
@require_POST
def revoke_api_key(request, slug, key_id):
    project = get_object_or_404(Project, slug=slug, user=request.user)
    key = get_object_or_404(APIKey, id=key_id, project=project)
    key.is_active = False
    key.save()
    return _json({'status': 'revoked'})


# ─── Table management ─────────────────────────────────────────────────────────

@login_required
@require_POST
def add_table(request, slug):
    project = get_object_or_404(Project, slug=slug, user=request.user)
    table_name = request.POST.get('table_name', '').strip().lower()
    sync_mode  = request.POST.get('sync_mode', 'manual')

    if not table_name:
        return _json({'error': 'Table name is required.'}, 400)
    if TableSyncConfig.objects.filter(project=project, table_name=table_name).exists():
        return _json({'error': f'Table "{table_name}" already exists in this project.'}, 400)

    t = TableSyncConfig.objects.create(
        project=project, table_name=table_name, sync_mode=sync_mode)
    return _json({'status': 'added', 'id': t.id, 'table_name': t.table_name,
                  'sync_mode': t.sync_mode, 'display': t.get_sync_mode_display()})


@login_required
@require_POST
def delete_table(request, slug, table_id):
    project = get_object_or_404(Project, slug=slug, user=request.user)
    t = get_object_or_404(TableSyncConfig, id=table_id, project=project)
    t.delete()
    return _json({'status': 'deleted'})


@login_required
@require_POST
def update_table(request, slug, table_id):
    project = get_object_or_404(Project, slug=slug, user=request.user)
    t = get_object_or_404(TableSyncConfig, id=table_id, project=project)
    sync_mode = request.POST.get('sync_mode', t.sync_mode)
    if sync_mode in dict(TableSyncConfig.SYNC_MODES):
        t.sync_mode = sync_mode
        t.save()
    return _json({'status': 'updated', 'sync_mode': t.sync_mode,
                  'display': t.get_sync_mode_display()})
