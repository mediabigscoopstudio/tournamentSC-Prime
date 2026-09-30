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
import os
import random

from google.oauth2 import id_token
from datetime import timedelta
from google.auth.transport import requests as google_requests
from django.contrib.auth import get_user_model, update_session_auth_hash

from django.core.mail import send_mail
from django.contrib import messages
from django.contrib.auth import login, logout
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .decorators import login_required_msg, organizer_area_required, player_required
from django.contrib.auth.forms import PasswordChangeForm
from .forms import (OrganizerApplicationForm, OrganizerLoginForm, OrganizerProfileForm,
                    OrganizerSignupForm, PlayerLoginForm, PlayerProfileForm, PlayerSignupForm, UserSettingsForm)
from .models import (AuditLog, Notification, OrganizerApplication, OrganizerProfile,
                     PlayerProfile, User, UserFCMToken, EmailOTP, UserSettings, UserFollow)


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
    if getattr(user, 'onboarding_complete', False) is False:
        return 'welcome_animation'
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


def logout_view(request):
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('home')

# ======================================================================
# Unified Auth Flow
# ======================================================================
def unified_login(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    form = PlayerLoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.cleaned_data.get('user')
        if user and not user.is_email_verified:
            # Need to verify OTP
            otp_val = str(random.randint(100000, 999999))
            EmailOTP.objects.create(email=user.email, otp=otp_val, expires_at=timezone.now() + timedelta(minutes=10))
            print(f"OTP for {user.email}: {otp_val}")  # Dev console fallback
            try:
                send_mail('Your TournamentSC Login Code', f'Your OTP is {otp_val}. It expires in 10 minutes.', None, [user.email])
            except Exception as e:
                print(f"Email error: {e}")
            request.session['auth_email'] = user.email
            return redirect('verify_otp')
        return _do_login(request, form, 'player_dashboard')
    
    return render(request, 'accounts/login.html', {'form': form, 'google_client_id': os.getenv('GOOGLE_CLIENT_ID', '')})

def unified_signup(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    conf = _settings()
    
    form = PlayerSignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.is_email_verified = False
        user.save()
        PlayerProfile.objects.get_or_create(user=user)
        
        # Generate OTP
        otp_val = str(random.randint(100000, 999999))
        EmailOTP.objects.create(email=user.email, otp=otp_val, expires_at=timezone.now() + timedelta(minutes=10))
        print(f"OTP for {user.email}: {otp_val}")  # Dev console fallback
        try:
            send_mail('Verify your TournamentSC Account', f'Your OTP is {otp_val}. It expires in 10 minutes.', None, [user.email])
        except Exception as e:
            print(f"Email error: {e}")
        
        request.session['auth_email'] = user.email
        return redirect('verify_otp')
        
    return render(request, 'accounts/signup.html', {'form': form, 'google_client_id': os.getenv('GOOGLE_CLIENT_ID', '')})

def verify_otp(request):
    email = request.session.get('auth_email')
    if not email:
        return redirect('login')
        
    if request.method == 'POST':
        otp_entered = request.POST.get('otp', '').strip()
        otp_record = EmailOTP.objects.filter(email=email, is_verified=False, expires_at__gte=timezone.now()).order_by('-created_at').first()
        
        if otp_record and otp_record.otp == otp_entered:
            otp_record.is_verified = True
            otp_record.save()
            
            user = get_user_model().objects.get(email=email)
            user.is_email_verified = True
            user.save(update_fields=['is_email_verified'])
            
            login(request, user)
            del request.session['auth_email']
            messages.success(request, f'Welcome, {user.display_name}!')
            return redirect('player_dashboard')
        else:
            messages.error(request, 'Invalid or expired OTP.')
            
    return render(request, 'accounts/verify_otp.html', {'email': email})

@require_POST
def google_login(request):
    token = request.POST.get('credential')
    try:
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), os.getenv('GOOGLE_CLIENT_ID'))
        email = idinfo['email']
        
        User = get_user_model()
        user = User.objects.filter(email=email).first()
        if not user:
            user = User.objects.create_user(email=email, password=None)
            user.is_email_verified = True
            user.save()
            PlayerProfile.objects.get_or_create(user=user)
        elif not user.is_email_verified:
            user.is_email_verified = True
            user.save(update_fields=['is_email_verified'])
            
        login(request, user)
        from django.shortcuts import resolve_url
        return JsonResponse({'status': 'success', 'redirect': resolve_url(_home_for(user))})
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'Invalid token'}, status=400)


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
# Settings
# ======================================================================
@login_required_msg
def settings_view(request):
    settings_obj, _ = UserSettings.objects.get_or_create(user=request.user)
    
    settings_form = UserSettingsForm(request.POST or None, instance=settings_obj, prefix='settings')
    password_form = PasswordChangeForm(request.user, request.POST or None, prefix='password')
    
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_settings' and settings_form.is_valid():
            settings_form.save()
            messages.success(request, 'Settings updated.')
            return redirect('settings_view')
        elif action == 'change_password' and password_form.is_valid():
            user = password_form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Password changed successfully.')
            return redirect('settings_view')
        elif action == 'disable_account':
            request.user.is_active = False
            request.user.save()
            logout(request)
            messages.info(request, 'Your account has been disabled.')
            return redirect('home')

    return render(request, 'accounts/settings.html', {
        'settings_form': settings_form,
        'password_form': password_form,
    })

# ======================================================================
# Social
# ======================================================================
@login_required_msg
@require_POST
def toggle_follow_user(request, pk):
    target_user = get_object_or_404(get_user_model(), pk=pk, is_active=True, is_suspended=False)
    if target_user == request.user:
        return JsonResponse({'error': 'Cannot follow yourself'}, status=400)
        
    follow, created = UserFollow.objects.get_or_create(follower=request.user, following=target_user)
    if not created:
        follow.delete()
        is_following = False
    else:
        is_following = True
        
    return JsonResponse({
        'is_following': is_following,
        'follower_count': target_user.followers_users.count()
    })

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

# ======================================================================
# Onboarding & API
# ======================================================================

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.contrib.auth.decorators import login_required
from .models import PlayerProfile

@require_GET
def check_username_api(request):
    username = request.GET.get('username', '').strip().lower()
    if not username:
        return JsonResponse({'available': False, 'valid': False, 'reason': 'empty', 'suggestions': []})
    
    import re
    if not re.match(r'^[a-z0-9_]+$', username):
        return JsonResponse({'available': False, 'valid': False, 'reason': 'invalid_format', 'suggestions': []})
    
    User = get_user_model()
    exists = User.objects.filter(username__iexact=username).exists()
    
    if not exists:
        return JsonResponse({'available': True, 'username': username, 'suggestions': []})
    
    # Generate suggestions
    suggestions = []
    base = username
    for i in range(1, 10):
        cand = f"{base}{i}"
        if not User.objects.filter(username__iexact=cand).exists():
            suggestions.append(cand)
        if len(suggestions) >= 3:
            break
    cand_chess = f"{base}_chess"
    if not User.objects.filter(username__iexact=cand_chess).exists():
        suggestions.append(cand_chess)
        
    return JsonResponse({'available': False, 'valid': True, 'reason': 'taken', 'suggestions': suggestions})


@login_required
def welcome_animation(request):
    if getattr(request.user, 'onboarding_complete', False):
        return redirect(_home_for(request.user))
    return render(request, 'accounts/welcome.html')


@login_required
def onboarding_flow(request):
    if getattr(request.user, 'onboarding_complete', False):
        return redirect(_home_for(request.user))
        
    if request.method == 'POST':
        # Handle the combined POST from the 5-step form
        user = request.user
        profile, _ = PlayerProfile.objects.get_or_create(user=user)
        
        # Step 1
        if 'first_name' in request.POST:
            user.first_name = request.POST.get('first_name', '')
        if 'last_name' in request.POST:
            user.last_name = request.POST.get('last_name', '')
        if 'username' in request.POST:
            desired_username = request.POST.get('username').strip().lower()
            if desired_username and not get_user_model().objects.filter(username__iexact=desired_username).exclude(pk=user.pk).exists():
                user.username = desired_username
                
        profile.middle_name = request.POST.get('middle_name', '')
        profile.date_of_birth = request.POST.get('date_of_birth') or None
        profile.gender = request.POST.get('gender', 'U')
        
        if 'profile_photo' in request.FILES:
            profile.profile_photo = request.FILES['profile_photo']
            
        # Step 2
        profile.bio = request.POST.get('bio', '')
        # (sports logic can be added later if multi-select is passed)
        
        # Step 3
        profile.school = request.POST.get('school', '')
        profile.college = request.POST.get('college', '')
        profile.workplace = request.POST.get('workplace', '')
        
        # Step 4
        profile.home_city = request.POST.get('home_city', '')
        profile.current_city = request.POST.get('current_city', '')
        
        user.onboarding_complete = True
        user.save()
        profile.save()
        
        return redirect('onboarding_success')
        
    return render(request, 'accounts/onboarding.html')

@login_required
def onboarding_success(request):
    return render(request, 'accounts/onboarding_success.html')
