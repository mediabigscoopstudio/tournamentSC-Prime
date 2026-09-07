"""Authentication + profile views.

Three isolated flows live here. Each has its own login page, its own sign-up
page and its own post-login destination; none of them can be used to reach
another role's area:

    /player/login     -> /player/            (player dashboard)
    /organizer/login  -> /organizer/         (organizer dashboard)
    /dashboard/login  -> /dashboard/         (platform admin console — in `dash`)

`/login` and `/signup` are *choosers*: plain public pages that point at the
right door. They never authenticate anyone themselves.
"""
import json

from django.contrib import messages
from django.contrib.auth import login, logout
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .decorators import login_required_msg, organizer_area_required, player_required
from .forms import (OrganizerApplicationForm, OrganizerLoginForm, OrganizerProfileForm,
                    OrganizerSignupForm, PlayerLoginForm, PlayerProfileForm, PlayerSignupForm)
from .models import (AuditLog, Notification, OrganizerApplication, OrganizerProfile,
                     PlayerProfile, User, UserFCMToken)


# ======================================================================
# Helpers
# ======================================================================
def _safe_next(request, fallback):
    """Only ever redirect to a path on this site."""
    nxt = request.POST.get('next') or request.GET.get('next')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return nxt
    return fallback


def _home_for(user):
    """Where a signed-in account belongs. The single source of truth for
    post-login routing, so no view has to re-derive it."""
    if user.is_staff:
        return 'dash_index'
    if user.has_organizer_profile:
        return 'organizer_dashboard' if user.is_approved_organizer else 'organizer_status'
    return 'player_dashboard'


def _settings():
    from dash.models import SiteSetting
    return SiteSetting.load()


def _do_login(request, form, fallback):
    user = form.cleaned_data['user']
    login(request, user)
    messages.success(request, f'Welcome back, {user.display_name}.')
    return redirect(_safe_next(request, fallback))


# ======================================================================
# Choosers (public, no authentication happens here)
# ======================================================================
def login_chooser(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    return render(request, 'accounts/login_chooser.html', {'next': request.GET.get('next', '')})


def signup_chooser(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    return render(request, 'accounts/signup_chooser.html', {'settings_obj': _settings()})


def logout_view(request):
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('home')


# ======================================================================
# Player flow
# ======================================================================
def player_login(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    form = PlayerLoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        return _do_login(request, form, 'player_dashboard')
    return render(request, 'accounts/player_login.html', {'form': form})


def player_signup(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    conf = _settings()
    if not conf.allow_player_registration:
        messages.warning(request, 'Player registration is currently closed.')
        return redirect('home')

    form = PlayerSignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        PlayerProfile.objects.get_or_create(user=user)
        login(request, user)
        messages.success(request, f'Welcome to TournamentSC, {user.display_name}!')
        return redirect(_safe_next(request, 'player_dashboard'))
    return render(request, 'accounts/player_signup.html', {'form': form})


@player_required
def profile_edit(request):
    profile, _ = PlayerProfile.objects.get_or_create(user=request.user)
    form = PlayerProfileForm(request.POST or None, request.FILES or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Profile updated.')
        return redirect('profile_edit')
    return render(request, 'accounts/profile_edit.html', {'form': form, 'profile': profile})


# ======================================================================
# Organizer flow
# ======================================================================
def organizer_login(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    form = OrganizerLoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.cleaned_data['user']
        # An account without organizer capability lands on the apply page rather
        # than a dashboard it cannot use.
        fallback = _home_for(user) if user.has_organizer_profile else 'organizer_apply'
        return _do_login(request, form, fallback)
    return render(request, 'accounts/organizer_login.html', {'form': form})


def organizer_signup(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    conf = _settings()
    if not conf.allow_organizer_registration:
        messages.warning(request, 'Organizer registration is currently closed.')
        return redirect('home')

    form = OrganizerSignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        if conf.auto_approve_organizers:
            profile = user.organizer_profile
            profile.is_approved = True
            profile.approved_at = timezone.now()
            profile.save(update_fields=['is_approved', 'approved_at', 'updated_at'])
            AuditLog.record(None, 'auto_approve_organizer', user.email,
                            'Auto-approved by application settings.')
            messages.success(request, 'Your organizer account is ready. Create your first tournament!')
            return redirect('organizer_dashboard')
        messages.success(request, 'Account created. Tell us about the events you want to run.')
        return redirect('organizer_apply')
    return render(request, 'accounts/organizer_signup.html', {'form': form})


@organizer_area_required
def organizer_apply(request):
    OrganizerProfile.objects.get_or_create(user=request.user)
    # Block a second pending/approved application.
    existing = request.user.organizer_applications.exclude(
        status=OrganizerApplication.STATUS_REJECTED).first()
    if existing or request.user.is_approved_organizer:
        return redirect('organizer_status')

    form = OrganizerApplicationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        app = form.save(commit=False)
        app.user = request.user
        app.save()
        form.save_m2m()
        messages.success(request, 'Application submitted. An admin will review it shortly.')
        return redirect('organizer_status')
    return render(request, 'accounts/organizer_apply.html', {'form': form})


@organizer_area_required
def organizer_status(request):
    profile = getattr(request.user, 'organizer_profile', None)
    return render(request, 'accounts/organizer_status.html', {
        'profile': profile,
        'application': request.user.organizer_applications.first(),
        'approved': request.user.is_approved_organizer,
    })


@organizer_area_required
def organizer_profile_edit(request):
    profile, _ = OrganizerProfile.objects.get_or_create(user=request.user)
    form = OrganizerProfileForm(request.POST or None, request.FILES or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Organizer profile updated.')
        return redirect('organizer_profile_edit')
    return render(request, 'accounts/organizer_profile.html', {'form': form, 'profile': profile})


# ======================================================================
# Public + shared
# ======================================================================
def player_public(request, pk):
    """Public player profile — audience, no login."""
    profile = get_object_or_404(
        PlayerProfile.objects.select_related('user'), pk=pk, user__is_suspended=False)
    from tournaments.stats import player_history
    return render(request, 'accounts/player_public.html', {
        'profile': profile,
        'history': player_history(profile),
        'earned_achievements': profile.user.achievements.select_related('achievement'),
    })


@login_required_msg
def notifications_list(request):
    qs = request.user.notifications.all()
    unread_ids = list(qs.filter(is_read=False).values_list('id', flat=True))
    if unread_ids:
        Notification.objects.filter(id__in=unread_ids).update(is_read=True)
    return render(request, 'accounts/notifications.html', {'items': qs})


@require_POST
@login_required_msg
def fcm_register_token(request):
    """Register/refresh this browser's FCM token for push delivery."""
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid JSON body.'}, status=400)

    token = (body.get('token') or '').strip()
    if not token:
        return JsonResponse({'error': 'token is required.'}, status=400)
    device_type = body.get('device_type', 'web')

    # The same physical browser can re-register this token under a different
    # account (shared device, sign-out/sign-in) — `token` is globally unique,
    # so drop any stale ownership before attaching it to the current user.
    UserFCMToken.objects.filter(token=token).exclude(user=request.user).delete()
    UserFCMToken.objects.update_or_create(
        user=request.user,
        defaults={'token': token, 'device_type': device_type, 'is_active': True})
    return JsonResponse({'status': 'registered'})
