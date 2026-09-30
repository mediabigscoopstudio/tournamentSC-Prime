"""Authentication routes.

Each role gets its own front door. `/login` and `/signup` are choosers only —
they authenticate nobody. The platform-admin door lives in `dash.urls`
(`/dashboard/login`) and is deliberately not reachable from here.
"""
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # --- Unified Auth ---
    path('login', views.unified_login, name='login'),
    path('signup', views.unified_signup, name='signup'),
    path('logout', views.logout_view, name='logout'),
    path('google-login', views.google_login, name='google_login'),
    path('verify-otp', views.verify_otp, name='verify_otp'),
    
    # --- Onboarding & API ---
    path('api/auth/username/check/', views.check_username_api, name='check_username_api'),
    path('welcome', views.welcome_animation, name='welcome_animation'),
    path('onboarding', views.onboarding_flow, name='onboarding_flow'),
    path('onboarding/success', views.onboarding_success, name='onboarding_success'),

    # --- Player profile ---
    path('player/profile', views.profile_edit, name='profile_edit'),

    # --- Organizer capability ---
    path('organizer/apply', views.organizer_apply, name='organizer_apply'),
    path('organizer/status', views.organizer_status, name='organizer_status'),
    path('organizer/profile', views.organizer_profile_edit, name='organizer_profile_edit'),


    # --- Settings ---
    path('settings', views.settings_view, name='settings_view'),

    # --- Social ---
    path('users/<int:pk>/toggle-follow', views.toggle_follow_user, name='toggle_follow_user'),
    # --- Public player profile (audience, no login) ---
    path('players/<int:pk>', views.player_public, name='player_public'),

    # --- Shared, authenticated ---
    path('notifications', views.notifications_list, name='notifications'),
    path('notifications/fcm-register', views.fcm_register_token, name='fcm_register_token'),

    # --- Password reset (Django built-ins, custom templates) ---
    path('password-reset', auth_views.PasswordResetView.as_view(
        template_name='accounts/password_reset.html',
        email_template_name='accounts/password_reset_email.html',
        success_url='/password-reset/done'), name='password_reset'),
    path('password-reset/done', auth_views.PasswordResetDoneView.as_view(
        template_name='accounts/password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>', auth_views.PasswordResetConfirmView.as_view(
        template_name='accounts/password_reset_confirm.html',
        success_url='/reset/done'), name='password_reset_confirm'),
    path('reset/done', auth_views.PasswordResetCompleteView.as_view(
        template_name='accounts/password_reset_complete.html'), name='password_reset_complete'),
]
