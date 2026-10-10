"""Reusable role/permission guards.

Three isolated authentication flows live behind these decorators:

    audience    -> no guard at all (public views are simply undecorated)
    player      -> @player_required          -> /player/login
    organizer   -> @approved_organizer_required -> /organizer/login
    platform admin -> @admin_required        -> /dashboard/login

Every decision is enforced here in the view layer, never only by hiding buttons.
A guard that fails sends the user to *its own* login page — the flows never
bleed into one another.
"""
from functools import wraps
from urllib.parse import quote

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse


def _login_redirect(url_name, request):
    """Send an anonymous visitor to the login page for *their* flow, keeping the
    page they wanted in `next`."""
    target = reverse(url_name)
    return redirect(f'{target}?next={quote(request.get_full_path())}')


def login_required_msg(view):
    """Any authenticated non-admin user. Used by pages shared by players and
    organizers (notifications, password change)."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.info(request, 'Please log in to continue.')
            return _login_redirect('login', request)
        if request.user.is_staff:
            # A platform admin belongs in the admin console, not the public app.
            return redirect('dash_index')
        return view(request, *args, **kwargs)
    return wrapped


def player_required(view):
    """The player flow. Platform admins are bounced to their own console."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.info(request, 'Log in to your player account to continue.')
            return _login_redirect('player_login', request)
        if request.user.is_staff:
            return redirect('dash_index')
        return view(request, *args, **kwargs)
    return wrapped


def approved_organizer_required(view):
    """The organizer flow. Requires an organizer profile that an admin approved,
    OR an active co-organizer delegation for the specific tournament being managed."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.info(request, 'Please log in to continue.')
            return _login_redirect('login', request)
        if request.user.is_staff:
            return redirect('dash_index')
        slug = kwargs.get('slug')
        if slug:
            from tournaments.models import TournamentCoOrganizer
            if TournamentCoOrganizer.objects.filter(tournament__slug=slug, user=request.user, is_active=True).exists():
                return view(request, *args, **kwargs)
        if not request.user.is_approved_organizer:
            messages.warning(request, 'You need an approved organizer account to do that.')
            return redirect('organizer_status')
        return view(request, *args, **kwargs)
    return wrapped


def organizer_area_required(view):
    """Inside the organizer area but *before* approval (apply / status pages)."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.info(request, 'Log in to your organizer account to continue.')
            return _login_redirect('organizer_login', request)
        if request.user.is_staff:
            return redirect('dash_index')
        return view(request, *args, **kwargs)
    return wrapped


def admin_required(view):
    """Platform admin = Django's `is_staff`.

    An anonymous visitor is sent to the admin login page. An authenticated
    *non*-admin (player or organizer) gets a hard 403 — the admin console is
    never advertised to them, and never redirects them anywhere useful.
    """
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _login_redirect('dash_login', request)
        if not request.user.is_staff or request.user.is_suspended:
            raise PermissionDenied('Admin access required.')
        return view(request, *args, **kwargs)
    return wrapped


def superadmin_required(view):
    """The most destructive controls (roles, permissions, settings) are reserved
    for a superuser, not every staff member."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _login_redirect('dash_login', request)
        if not request.user.is_superuser:
            raise PermissionDenied('Super administrator access required.')
        return view(request, *args, **kwargs)
    return wrapped
