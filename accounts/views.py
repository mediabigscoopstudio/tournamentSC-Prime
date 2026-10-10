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
import re
from django.conf import settings
from tournaments.tasks import _send
import os
import random

from google.oauth2 import id_token
from datetime import timedelta
from google.auth.transport import requests as google_requests
from django.contrib.auth import get_user_model, update_session_auth_hash

from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.contrib.auth import login, logout
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .decorators import login_required_msg, organizer_area_required, player_required
from django.contrib.auth.forms import PasswordChangeForm
from .forms import (OrganizerApplicationForm, OrganizerLoginForm, OrganizerProfileForm,
                    OrganizerSignupForm, PlayerLoginForm, PlayerProfileForm, PlayerSignupForm,
                    UserSettingsForm, RefereeProfileForm, CommentatorProfileForm)
from .models import (AuditLog, Notification, OrganizerApplication, OrganizerProfile,
                     PlayerProfile, User, UserFCMToken, EmailOTP, UserSettings, UserFollow,
                     RefereeProfile, CommentatorProfile)


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
    return 'content_feed'


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
            # Existing account that never verified its email
            request.session['auth_email'] = user.email
            _issue_otp(user.email, user.username)
            return redirect('verify_otp')
        return _do_login(request, form, _home_for(form.cleaned_data['user']))
    
    return render(request, 'accounts/login.html', {'form': form, 'google_client_id': os.getenv('GOOGLE_CLIENT_ID', '')})

USERNAME_RE = re.compile(r'^[a-z0-9_]{3,30}$')


def _issue_otp(email, username=''):
    """Create a fresh OTP, invalidate older ones and email it. Returns (otp, sent_ok)."""
    EmailOTP.objects.filter(email=email, is_verified=False).update(expires_at=timezone.now())
    otp_val = str(random.randint(100000, 999999))
    EmailOTP.objects.create(email=email, otp=otp_val, expires_at=timezone.now() + timedelta(minutes=10))
    print(f"OTP for {email}: {otp_val}")  # Dev console fallback
    try:
        _send('signup_otp', f'{otp_val} is your TournamentSC verification code', email,
              {'otp': otp_val, 'username': username})
        return otp_val, True
    except Exception as e:
        print(f"OTP email error: {e}")
        return otp_val, False


def unified_signup(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    ctx = {'google_client_id': os.getenv('GOOGLE_CLIENT_ID', '')}
    User = get_user_model()

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        error = None
        if not email or '@' not in email:
            error = 'Enter a valid email address.'
        elif User.objects.filter(email__iexact=email).exists():
            error = 'An account with this email already exists. Try signing in.'
        else:
            try:
                validate_password(password)
            except ValidationError as e:
                error = ' '.join(e.messages)

        if error:
            messages.error(request, error)
            ctx.update(prefill_email=email, start_step='step-email')
            return render(request, 'accounts/signup.html', ctx)

        # The account is only created after the OTP is verified; hold details in the session.
        request.session['pending_signup'] = {
            'email': email, 'password_hash': make_password(password)}
        request.session['auth_email'] = email
        _, sent = _issue_otp(email)
        if not sent:
            messages.warning(request, "We couldn't send the email right now. Tap 'Resend code' in a moment.")
        return redirect('verify_otp')

    return render(request, 'accounts/signup.html', ctx)


def verify_otp(request):
    email = request.session.get('auth_email')
    pending = request.session.get('pending_signup')
    User = get_user_model()
    legacy = None
    if email and not pending:
        legacy = User.objects.filter(email__iexact=email, is_email_verified=False).first()
    if not email or (not pending and not legacy):
        messages.error(request, 'Your signup session expired. Please start again.')
        return redirect('signup')
    uname = legacy.username if legacy else ''

    if request.method == 'POST':
        if request.POST.get('action') == 'resend':
            _, sent = _issue_otp(email, uname)
            messages.success(request, 'A new code has been sent.') if sent else \
                messages.error(request, "Couldn't send the email. Please try again shortly.")
            return redirect('verify_otp')

        otp_entered = request.POST.get('otp', '').strip()
        rec = EmailOTP.objects.filter(email=email, is_verified=False,
                                      expires_at__gte=timezone.now()).order_by('-created_at').first()
        if rec and rec.otp == otp_entered:
            rec.is_verified = True
            rec.save(update_fields=['is_verified'])
            if legacy:
                legacy.is_email_verified = True
                legacy.save(update_fields=['is_email_verified'])
                login(request, legacy, backend='django.contrib.auth.backends.ModelBackend')
                request.session.pop('auth_email', None)
                return redirect(_home_for(legacy))
            if User.objects.filter(email__iexact=email).exists():
                messages.error(request, 'That account was just created. Please sign in.')
                request.session.pop('pending_signup', None)
                request.session.pop('auth_email', None)
                return redirect('login')
            user = User.objects.create_user(email=email, password=None)
            user.password = pending['password_hash']
            user.is_email_verified = True
            user.save()
            PlayerProfile.objects.get_or_create(user=user)
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            request.session.pop('pending_signup', None)
            request.session.pop('auth_email', None)
            return redirect(_home_for(user))
        messages.error(request, 'Invalid or expired code.')

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
        return redirect(request.POST.get('next', 'profile_edit'))
    return render(request, 'accounts/profile_edit.html', {'form': form, 'profile': profile})



@login_required_msg
def edit_profile_quick(request):
    if request.method == 'POST':
        profile = request.user.player_profile
        user = request.user
        
        if 'profile_photo' in request.FILES:
            profile.profile_photo = request.FILES['profile_photo']
        if 'bio' in request.POST:
            profile.bio = request.POST['bio'].strip()
        if 'current_city' in request.POST:
            profile.current_city = request.POST['current_city'].strip()
        
        if 'first_name' in request.POST:
            user.first_name = request.POST['first_name'].strip()
        if 'last_name' in request.POST:
            user.last_name = request.POST['last_name'].strip()
        if 'username' in request.POST:
            username = request.POST['username'].strip()
            if username and not get_user_model().objects.filter(username__iexact=username).exclude(pk=user.pk).exists():
                user.username = username
        
        user.save()
        profile.save()
        messages.success(request, 'Profile updated successfully.')
        
    return redirect(request.POST.get('next', 'profile_edit'))

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
    """Public player profile — sports identity, community, and content journey."""
    profile = get_object_or_404(
        PlayerProfile.objects.select_related('user').prefetch_related('sports'),
        pk=pk, user__is_suspended=False
    )
    from tournaments.stats import player_history, player_results
    from tournaments.models import Standing

    is_following = False
    if request.user.is_authenticated:
        from accounts.models import UserFollow
        is_following = UserFollow.objects.filter(follower=request.user, following=profile.user).exists()
    
    content_posts = profile.user.content_posts.filter(
        is_published=True, is_removed=False
    ).prefetch_related('media_items', 'tagged_users').order_by('-created_at')
    
    all_posts = list(content_posts)
    # Segregate content into distinct sports media categories:
    # 1. Vlogs: 16:9 full length videos or youtube embeds
    vlogs = [p for p in all_posts if p.content_type == 'full_video' or p.aspect_ratio == '16/9' or bool(p.youtube_url)]
    # 2. Highlights: 9:16 short vertical videos
    highlights = [p for p in all_posts if (p.content_type == 'short' or p.aspect_ratio == '9/16') and p not in vlogs]
    # 3. Posts: photos and carousels
    posts = [p for p in all_posts if p not in vlogs and p not in highlights]

    # Sporting history and genuine metrics
    history = player_history(profile)
    matches_played_count = player_results(profile).count()
    standings = Standing.objects.filter(player=profile)
    total_wins = sum(s.won for s in standings)

    # Teams and achievements
    teams = profile.team_memberships.filter(is_approved=True).select_related('team', 'team__sport')
    earned_achievements = profile.user.achievements.select_related('achievement').order_by('-created_at')
    
    return render(request, 'accounts/player_public.html', {
        'profile': profile,
        'history': history,
        'matches_played_count': matches_played_count,
        'total_wins': total_wins,
        'earned_achievements': earned_achievements,
        'teams': teams,
        'follower_count': profile.user.followers_users.count(),
        'following_count': profile.user.following_users.count(),
        'is_following': is_following,
        'content_posts': content_posts,
        'vlogs': vlogs,
        'highlights': highlights,
        'posts': posts,
        'has_any_content': bool(all_posts),
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
    if not USERNAME_RE.match(username):
        return JsonResponse({'available': False, 'valid': False, 'reason': 'invalid_format', 'suggestions': []})
    
    User = get_user_model()
    qs = User.objects.filter(username__iexact=username)
    if request.user.is_authenticated:
        qs = qs.exclude(pk=request.user.pk)
    exists = qs.exists()
    
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


def _to_square_webp(upload, size=512):
    """Centre-crop to a square, resize, and re-encode as WebP. Returns a
    ContentFile ready for an ImageField, or None if it isn't a valid image."""
    from io import BytesIO
    from uuid import uuid4
    from PIL import Image, ImageOps
    from django.core.files.base import ContentFile
    try:
        img = ImageOps.exif_transpose(Image.open(upload))
        img = img.convert('RGBA' if 'A' in img.getbands() else 'RGB')
        img = ImageOps.fit(img, (size, size), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format='WEBP', quality=85)
        return ContentFile(buf.getvalue(), name=f'{uuid4().hex}.webp')
    except Exception:
        return None


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
        desired_username = request.POST.get('username', '').strip().lower()
        uerr = None
        if not USERNAME_RE.match(desired_username):
            uerr = 'Choose a username of 3-30 characters: letters, numbers or underscore.'
        elif get_user_model().objects.filter(username__iexact=desired_username).exclude(pk=user.pk).exists():
            uerr = 'That username is already taken. Please pick another.'
        if uerr:
            messages.error(request, uerr)
            return render(request, 'accounts/onboarding.html', {'username_error': uerr, 'posted': request.POST})
        user.username = desired_username

        profile.middle_name = request.POST.get('middle_name', '')
        profile.date_of_birth = request.POST.get('date_of_birth') or None
        profile.gender = request.POST.get('gender', 'U')
        
        if 'profile_photo' in request.FILES:
            webp = _to_square_webp(request.FILES['profile_photo'])
            if webp:
                profile.profile_photo = webp
            
        
        # Step 2
        profile.bio = request.POST.get('bio', '')
        sport_names = request.POST.getlist('sports')
        if sport_names:
            from tournaments.models import Sport
            from django.utils.text import slugify
            sport_objs = []
            for name in sport_names:
                name = name.strip()
                if not name: continue
                s, _ = Sport.objects.get_or_create(name__iexact=name, defaults={'name': name, 'slug': slugify(name)})
                sport_objs.append(s)
            profile.sports.set(sport_objs)

        
        # Step 3
        profile.school = [s.strip() for s in request.POST.getlist('school[]') if s.strip()]
        profile.college = [c.strip() for c in request.POST.getlist('college[]') if c.strip()]
        
        workplaces = []
        wp_names = request.POST.getlist('workplace_name[]')
        wp_descs = request.POST.getlist('workplace_desc[]')
        for i in range(len(wp_names)):
            if wp_names[i].strip():
                workplaces.append({
                    'name': wp_names[i].strip(),
                    'description': wp_descs[i].strip() if i < len(wp_descs) else ''
                })
        profile.workplace = workplaces
        
        # Step 4
        profile.home_city = request.POST.get('home_city', '')
        profile.current_city = request.POST.get('current_city', '')
        
        user.onboarding_complete = True
        user.save()
        profile.save()
        
        # Dispatch welcome email using the centralized _send helper
        try:
            _send('platform_welcome', 'Welcome to TournamentSC! 🏆', user.email, {'user': user})
            print(f"Dispatched platform welcome email to {user.email}")
        except Exception as e:
            print(f"Failed to send welcome email: {e}")
        
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            from django.http import JsonResponse
            return JsonResponse({'status': 'success'})
        return redirect('onboarding_success')
        
    return render(request, 'accounts/onboarding.html')

@login_required
def onboarding_success(request):
    return render(request, 'accounts/onboarding_success.html')


@login_required_msg
def referee_onboarding(request):
    """Allows existing user to set up their referee profile and enter the referee ecosystem."""
    if hasattr(request.user, 'referee_profile'):
        return redirect('referee_dashboard')

    if request.method == 'POST':
        form = RefereeProfileForm(request.POST)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            profile.save()
            form.save_m2m()
            messages.success(request, 'Referee profile created! Welcome to your Referee Portal.')
            return redirect('referee_dashboard')
    else:
        form = RefereeProfileForm()

    return render(request, 'accounts/referee_onboarding.html', {'form': form, 'is_edit': False})


@login_required_msg
def referee_profile_edit(request):
    """Allows a referee to update their officiating details and availability."""
    profile = get_object_or_404(RefereeProfile, user=request.user)
    if request.method == 'POST':
        form = RefereeProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Referee profile updated successfully.')
            return redirect('referee_dashboard')
    else:
        form = RefereeProfileForm(instance=profile)

    return render(request, 'accounts/referee_onboarding.html', {
        'form': form,
        'is_edit': True,
        'profile': profile,
    })


def referee_public(request, pk):
    """Public referee profile: identity, qualifications, verified badge, stats, and match history."""
    profile = get_object_or_404(RefereeProfile.objects.select_related('user'), pk=pk)
    
    assignments = profile.user.referee_assignments.select_related(
        'fixture__tournament__sport', 'fixture__tournament__venue',
    ).prefetch_related(
        'fixture__participants__team', 'fixture__participants__player__user'
    ).order_by('-fixture__scheduled_time', '-id')

    total_matches = assignments.count()
    completed_matches = assignments.filter(fixture__status='COMPLETED')
    upcoming_matches = assignments.filter(fixture__status__in=['SCHEDULED', 'LIVE'])

    return render(request, 'accounts/referee_public.html', {
        'referee': profile,
        'total_matches': total_matches,
        'completed_matches': completed_matches,
        'upcoming_matches': upcoming_matches,
        'recent_assignments': assignments[:20],
    })


@login_required_msg
def commentator_onboarding(request):
    """Commentator profile creation flow.
    Preserves 1-account-many-capabilities architecture: any registered user
    can activate their commentator capabilities.
    """
    if hasattr(request.user, 'commentator_profile'):
        return redirect('commentator_dashboard')

    if request.method == 'POST':
        form = CommentatorProfileForm(request.POST)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            profile.save()
            form.save_m2m()
            messages.success(request, 'Commentator profile created! Welcome to the Commentator Portal.')
            return redirect('commentator_dashboard')
    else:
        form = CommentatorProfileForm()

    return render(request, 'accounts/commentator_onboarding.html', {'form': form, 'is_edit': False})


@login_required_msg
def commentator_profile_edit(request):
    """Allows a commentator to update their broadcasting details, reel, and availability."""
    profile = get_object_or_404(CommentatorProfile, user=request.user)
    if request.method == 'POST':
        form = CommentatorProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Commentator profile updated successfully.')
            return redirect('commentator_dashboard')
    else:
        form = CommentatorProfileForm(instance=profile)

    return render(request, 'accounts/commentator_onboarding.html', {
        'form': form,
        'is_edit': True,
        'profile': profile,
    })


def commentator_public(request, pk):
    """Public commentator portfolio: bio, languages, reel, stats, and match commentary history."""
    profile = get_object_or_404(CommentatorProfile.objects.select_related('user'), pk=pk)

    assignments = profile.user.commentator_assignments.select_related(
        'fixture__tournament__sport', 'fixture__tournament__venue',
    ).prefetch_related(
        'fixture__participants__team', 'fixture__participants__player__user'
    ).order_by('-fixture__scheduled_time', '-id')

    total_matches = assignments.count()
    completed_matches = assignments.filter(fixture__status='COMPLETED')
    upcoming_matches = assignments.filter(fixture__status__in=['SCHEDULED', 'LIVE'])

    return render(request, 'accounts/commentator_public.html', {
        'commentator': profile,
        'total_matches': total_matches,
        'completed_matches': completed_matches,
        'upcoming_matches': upcoming_matches,
        'recent_assignments': assignments[:20],
    })


