"""Authentication routes.

Each role gets its own front door. `/login` and `/signup` are choosers only —
they authenticate nobody. The platform-admin door lives in `dash.urls`
(`/dashboard/login`) and is deliberately not reachable from here.
"""
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # --- Choosers (public) ---
    path('login', views.login_chooser, name='login'),
    path('signup', views.signup_chooser, name='signup'),
    path('logout', views.logout_view, name='logout'),

    # --- Player authentication ---
    path('player/login', views.player_login, name='player_login'),
    path('player/register', views.player_signup, name='player_signup'),
    path('player/logout', views.logout_view, name='player_logout'),
    path('player/profile', views.profile_edit, name='profile_edit'),

    # --- Organizer authentication ---
    path('organizer/login', views.organizer_login, name='organizer_login'),
    path('organizer/register', views.organizer_signup, name='organizer_signup'),
    path('organizer/logout', views.logout_view, name='organizer_logout'),
    path('organizer/apply', views.organizer_apply, name='organizer_apply'),
    path('organizer/status', views.organizer_status, name='organizer_status'),
    path('organizer/profile', views.organizer_profile_edit, name='organizer_profile_edit'),

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
