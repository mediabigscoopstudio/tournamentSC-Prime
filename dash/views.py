"""The platform-admin application.

This is a self-contained admin *application*, not Django's built-in admin and
not a section of the public site. It has its own login page, its own base
template, its own navigation, and its own permission gate (`is_staff`). A
player or an organizer who guesses a URL in here gets a 403 — never a redirect
into a useful page, and never Django's admin login form.

Almost every screen is driven by the declarative registry in `resources.py`:
`resource_list`, `resource_form` and `resource_delete` below are the only CRUD
views in the codebase, and they serve all eighteen managed resources.
"""
import csv
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required, superadmin_required
from accounts.forms import AdminLoginForm
from accounts.models import (AuditLog, Follow, OrganizerApplication, OrganizerProfile,
                             PlayerProfile, User)
from tournaments.models import Fixture, Highlight, Sport, Team, Tournament

from .forms import SiteSettingForm
from .models import News, SiteSetting
from .resources import RESOURCES


# ======================================================================
# Branded admin authentication — completely separate from the public app
# ======================================================================
def dash_login(request):
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('dash_index')
    form = AdminLoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.cleaned_data['user']
        login(request, user)
        AuditLog.record(user, 'admin_login', user.email)
        nxt = request.GET.get('next') or request.POST.get('next')
        # Only ever bounce back into the admin console.
        return redirect(nxt if (nxt or '').startswith('/dashboard') else 'dash_index')
    return render(request, 'dash/signin.html', {'form': form})


def dash_logout(request):
    logout(request)
    return redirect('dash_login')


# ======================================================================
# Navigation (built once, from the registry)
# ======================================================================
GROUP_ORDER = ['Competition', 'People', 'Support', 'Content', 'Catalogue', 'System']


def _nav(user):
    """Sidebar: the registry's resources plus the hand-built pages."""
    groups = {name: [] for name in GROUP_ORDER}
    for res in RESOURCES.values():
        if res.superuser_only and not user.is_superuser:
            continue
        groups[res.group].append({
            'key': res.key, 'label': res.label, 'icon': res.icon,
            'url': f'/dashboard/{res.key}/',
        })
    groups['System'].append({'key': 'permissions', 'label': 'Permissions', 'icon': '🧩',
                             'url': '/dashboard/permissions/'})
    groups['System'].append({'key': 'analytics', 'label': 'Analytics', 'icon': '📈',
                             'url': '/dashboard/analytics/'})
    groups['System'].append({'key': 'reports', 'label': 'Reports', 'icon': '📤',
                             'url': '/dashboard/reports/'})
    groups['System'].append({'key': 'logs', 'label': 'Activity log', 'icon': '🗒️',
                             'url': '/dashboard/logs/'})
    if user.is_superuser:
        groups['System'].append({'key': 'settings', 'label': 'Settings', 'icon': '⚙️',
                                 'url': '/dashboard/settings/'})
    return [{'name': name, 'items': groups[name]} for name in GROUP_ORDER if groups[name]]


def _base_ctx(request, active=''):
    return {'nav_groups': _nav(request.user), 'active': active,
            'pending_apps': OrganizerApplication.objects.filter(status='PENDING').count()}


def _get_resource(request, key):
    res = RESOURCES.get(key)
    if res is None:
        raise Http404('No such resource.')
    if res.superuser_only and not request.user.is_superuser:
        raise PermissionDenied('Super administrator access required.')
    return res


# ======================================================================
# Overview
# ======================================================================
@admin_required
def dash_index(request):
    t = Tournament.objects.all()
    approved_orgs = OrganizerProfile.objects.filter(is_approved=True)

    # Organizer activation: approved organizers who created a tournament within 7 days.
    activated = 0
    for op in approved_orgs.filter(approved_at__isnull=False).prefetch_related('tournaments'):
        window_end = op.approved_at + timedelta(days=7)
        if any(x.created_at <= window_end for x in op.tournaments.all()):
            activated += 1
    total_approved = approved_orgs.count()

    by_sport = list(Sport.objects.annotate(
        n=Count('tournaments', filter=Q(tournaments__is_removed=False))).order_by('-n'))

    ctx = _base_ctx(request, 'home')
    ctx.update({
        'total_tournaments': t.count(),
        'completed': t.filter(status='COMPLETED').count(),
        'ongoing': t.filter(status='ONGOING').count(),
        'cancelled': t.filter(status='CANCELLED').count(),
        'draft': t.filter(status='DRAFT').count(),
        'upcoming': t.filter(status='PUBLISHED').count(),
        'active_organizers': total_approved,
        'total_users': User.objects.count(),
        'total_players': PlayerProfile.objects.count(),
        'total_teams': Team.objects.count(),
        'live_now': Fixture.objects.filter(status='LIVE', is_removed=False).count(),
        'activation_rate': round((activated / total_approved) * 100) if total_approved else 0,
        'by_sport': by_sport,
        'max_sport': max((s.n for s in by_sport), default=1) or 1,
        'recent_audit': AuditLog.objects.select_related('actor')[:8],
        'pending_registrations': (
            Tournament.objects.filter(registrations__status='PENDING').count() +
            Tournament.objects.filter(team_entries__status='PENDING').count()),
        'unpublished_news': News.objects.filter(status='DRAFT', is_archived=False).count(),
    })
    return render(request, 'dash/index.html', ctx)


# ======================================================================
# Generic CRUD — serves every resource in the registry
# ======================================================================
def _apply_filters(res, qs, request):
    for param, _label, _choices, orm_field in res.filters:
        value = request.GET.get(param, '').strip()
        if not value:
            continue
        if orm_field is None:
            qs = _custom_filter(res, qs, param, value)
        elif value in ('0', '1') and _is_boolean_field(res.model, orm_field):
            qs = qs.filter(**{orm_field: value == '1'})
        else:
            qs = qs.filter(**{orm_field: value})
    return qs


def _is_boolean_field(model, name):
    try:
        return model._meta.get_field(name).get_internal_type() == 'BooleanField'
    except Exception:
        return False


def _custom_filter(res, qs, param, value):
    """Filters that don't map to a single ORM field."""
    if res.key == 'users' and param == 'role':
        if value == 'staff':
            return qs.filter(is_staff=True)
        if value == 'organizer':
            return qs.filter(organizer_profile__isnull=False, is_staff=False)
        if value == 'player':
            return qs.filter(organizer_profile__isnull=True, is_staff=False)
    return qs


def _rows(res, objects):
    """Pre-render each row so the template stays free of logic."""
    rows = []
    for obj in objects:
        rows.append({
            'obj': obj,
            'pk': obj.pk,
            'cells': [{'value': col.value(obj), 'kind': col.kind} for col in res.columns],
            'actions': [a for a in res.actions if a.visible(obj)],
        })
    return rows


@admin_required
def resource_list(request, key):
    res = _get_resource(request, key)
    qs = res.search(res.queryset(), request.GET.get('q', '').strip())
    qs = _apply_filters(res, qs, request)

    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    querystring = request.GET.copy()
    querystring.pop('page', None)

    ctx = _base_ctx(request, key)
    ctx.update({
        'res': res,
        'page': page,
        'rows': _rows(res, page.object_list),
        'q': request.GET.get('q', ''),
        'active_filters': {p: request.GET.get(p, '') for p, _l, _c, _f in res.filters},
        'querystring': querystring.urlencode(),
        'total': qs.count(),
    })
    return render(request, 'dash/resource_list.html', ctx)


def _build_form(res, request, instance=None):
    kwargs = {'instance': instance}
    if request.method == 'POST':
        kwargs['data'] = request.POST
        kwargs['files'] = request.FILES
    # AdminUserForm needs to know who is editing, to stop self-lockout.
    if res.key == 'users':
        kwargs['request_user'] = request.user
    return res.form(**kwargs)


@admin_required
def resource_form(request, key, pk=None):
    res = _get_resource(request, key)
    if res.form is None:
        raise Http404('This resource is not editable here.')

    instance = None
    if pk is not None:
        if not res.can_edit:
            raise PermissionDenied('This resource cannot be edited.')
        instance = get_object_or_404(res.model, pk=pk)
    elif not res.can_create:
        raise PermissionDenied('This resource cannot be created here.')

    form = _build_form(res, request, instance)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        # Stamp authorship where the model records it.
        for attr in ('created_by', 'uploaded_by'):
            if instance is None and hasattr(obj, attr) and getattr(obj, attr) is None:
                setattr(obj, attr, request.user)
                obj.save(update_fields=[attr])
        AuditLog.record(request.user,
                        f'{"create" if instance is None else "update"}_{res.key}', str(obj))
        messages.success(request,
                         f'{res.singular} {"created" if instance is None else "updated"}.')
        return redirect('dash_resource_list', key=res.key)

    ctx = _base_ctx(request, key)
    ctx.update({'res': res, 'form': form, 'instance': instance,
                'is_create': instance is None})
    return render(request, 'dash/resource_form.html', ctx)


@admin_required
def resource_delete(request, key, pk):
    res = _get_resource(request, key)
    if not res.can_delete:
        raise PermissionDenied('This resource cannot be deleted.')
    obj = get_object_or_404(res.model, pk=pk)

    if request.method == 'POST':
        if res.key == 'users':
            if obj.pk == request.user.pk:
                messages.error(request, 'You cannot delete your own account.')
                return redirect('dash_resource_list', key=res.key)
            if obj.is_superuser and not request.user.is_superuser:
                raise PermissionDenied('Only a superuser can delete a superuser.')
        label = str(obj)
        try:
            obj.delete()
        except Exception as exc:  # protected FK, e.g. a Sport still used by tournaments
            messages.error(request, f'Cannot delete {label}: {exc}')
            return redirect('dash_resource_list', key=res.key)
        AuditLog.record(request.user, f'delete_{res.key}', label)
        messages.warning(request, f'Deleted {label}.')
        return redirect('dash_resource_list', key=res.key)

    ctx = _base_ctx(request, key)
    ctx.update({'res': res, 'obj': obj, 'related': _related_summary(obj)})
    return render(request, 'dash/resource_delete.html', ctx)


def _related_summary(obj):
    """What else would go if this row went — shown on the delete confirmation."""
    out = []
    for rel in obj._meta.related_objects:
        try:
            accessor = rel.get_accessor_name()
            manager = getattr(obj, accessor, None)
            if manager is None:
                continue
            count = manager.count() if hasattr(manager, 'count') else (1 if manager else 0)
            if count:
                out.append(f'{count} × {rel.related_model._meta.verbose_name_plural}')
        except Exception:
            continue
    return out


@admin_required
@require_POST
def resource_action(request, key, pk, action):
    res = _get_resource(request, key)
    if res.apply_action is None:
        raise Http404('This resource has no actions.')
    obj = get_object_or_404(res.model, pk=pk)
    try:
        message = res.apply_action(request, obj, action)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, message)
    return redirect(request.META.get('HTTP_REFERER') or f'/dashboard/{res.key}/')


# ======================================================================
# Permissions (read-only view of the framework's permission table)
# ======================================================================
@superadmin_required
def permissions_view(request):
    perms = Permission.objects.select_related('content_type').prefetch_related('group_set')
    q = request.GET.get('q', '').strip()
    if q:
        perms = perms.filter(Q(name__icontains=q) | Q(codename__icontains=q) |
                             Q(content_type__app_label__icontains=q))
    by_app = {}
    for p in perms.order_by('content_type__app_label', 'content_type__model', 'codename'):
        by_app.setdefault(p.content_type.app_label, []).append({
            'perm': p, 'roles': list(p.group_set.all())})
    ctx = _base_ctx(request, 'permissions')
    ctx.update({'by_app': by_app, 'q': q, 'roles': Group.objects.all()})
    return render(request, 'dash/permissions.html', ctx)


# ======================================================================
# Analytics
# ======================================================================
@admin_required
def analytics(request):
    now = timezone.now()
    t = Tournament.objects.filter(is_removed=False)

    by_sport = list(Sport.objects.annotate(
        n=Count('tournaments', filter=Q(tournaments__is_removed=False))).order_by('-n'))
    by_status = [(label, t.filter(status=code).count())
                 for code, label in [('DRAFT', 'Draft'), ('PUBLISHED', 'Upcoming'),
                                     ('ONGOING', 'Live'), ('COMPLETED', 'Completed'),
                                     ('CANCELLED', 'Cancelled')]]

    # Sign-ups over the last 12 weeks.
    signups = []
    for w in range(11, -1, -1):
        start = now - timedelta(weeks=w + 1)
        end = now - timedelta(weeks=w)
        signups.append({
            'label': end.strftime('%d %b'),
            'n': User.objects.filter(date_joined__gt=start, date_joined__lte=end).count(),
        })
    max_signups = max((s['n'] for s in signups), default=1) or 1

    top_organizers = list(OrganizerProfile.objects.filter(is_approved=True).annotate(
        n=Count('tournaments', filter=Q(tournaments__is_removed=False))
    ).select_related('user').order_by('-n')[:8])

    ctx = _base_ctx(request, 'analytics')
    ctx.update({
        'by_sport': by_sport,
        'max_sport': max((s.n for s in by_sport), default=1) or 1,
        'by_status': by_status,
        'max_status': max((n for _l, n in by_status), default=1) or 1,
        'signups': signups,
        'max_signups': max_signups,
        'top_organizers': top_organizers,
        'max_organizer': max((o.n for o in top_organizers), default=1) or 1,
        'totals': {
            'users': User.objects.count(),
            'players': PlayerProfile.objects.count(),
            'organizers': OrganizerProfile.objects.filter(is_approved=True).count(),
            'tournaments': t.count(),
            'fixtures': Fixture.objects.filter(is_removed=False).count(),
            'completed_fixtures': Fixture.objects.filter(status='COMPLETED',
                                                         is_removed=False).count(),
            'teams': Team.objects.count(),
            'highlights': Highlight.objects.filter(is_removed=False).count(),
            'follows': Follow.objects.count(),
            'highlight_views': Highlight.objects.filter(is_removed=False).aggregate(
                n=Sum('view_count'))['n'] or 0,
            'prize_money': Tournament.objects.filter(is_removed=False).aggregate(
                n=Sum('prize_pool'))['n'] or 0,
        },
    })
    return render(request, 'dash/analytics.html', ctx)


# ======================================================================
# Reports (CSV export of any resource)
# ======================================================================
@admin_required
def reports(request):
    ctx = _base_ctx(request, 'reports')
    ctx['exportable'] = [
        {'key': r.key, 'label': r.label, 'icon': r.icon, 'count': r.model._default_manager.count()}
        for r in RESOURCES.values()
        if not (r.superuser_only and not request.user.is_superuser)
    ]
    return render(request, 'dash/reports.html', ctx)


@admin_required
def report_export(request, key):
    """Stream any registered resource out as CSV, honouring the current filters."""
    res = _get_resource(request, key)
    qs = res.search(res.queryset(), request.GET.get('q', '').strip())
    qs = _apply_filters(res, qs, request)

    response = HttpResponse(content_type='text/csv')
    stamp = timezone.localtime(timezone.now()).strftime('%Y%m%d-%H%M')
    response['Content-Disposition'] = f'attachment; filename="{res.key}-{stamp}.csv"'

    writer = csv.writer(response)
    writer.writerow(['id'] + [c.label for c in res.columns])
    for obj in qs[:5000]:
        writer.writerow([obj.pk] + [c.value(obj) for c in res.columns])

    AuditLog.record(request.user, 'export_csv', res.label, f'{qs.count()} rows')
    return response


# ======================================================================
# Activity log
# ======================================================================
@admin_required
def audit_log(request):
    qs = AuditLog.objects.select_related('actor')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(target__icontains=q) |
                       Q(actor__email__icontains=q))
    page = Paginator(qs, 50).get_page(request.GET.get('page'))
    ctx = _base_ctx(request, 'logs')
    ctx.update({'page': page, 'q': q})
    return render(request, 'dash/audit.html', ctx)


# ======================================================================
# Site + application settings
# ======================================================================
@superadmin_required
def settings_view(request):
    conf = SiteSetting.load()
    form = SiteSettingForm(request.POST or None, instance=conf)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.updated_by = request.user
        obj.save()
        AuditLog.record(request.user, 'update_settings', 'Site & application settings')
        messages.success(request, 'Settings saved.')
        return redirect('dash_settings')
    ctx = _base_ctx(request, 'settings')
    ctx.update({'form': form, 'conf': conf})
    return render(request, 'dash/settings.html', ctx)
