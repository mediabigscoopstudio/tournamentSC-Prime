"""Root URL configuration for TournamentSC.

Two applications, four isolated route namespaces:

    /                 public audience site        — no authentication, ever
    /player/…         player application          — /player/login
    /organizer/…      organizer application       — /organizer/login
    /dashboard/…      platform-admin application  — /dashboard/login

Include order matters. `accounts` claims the auth doors first, `tournaments`
claims the organizer + player action routes, `dash` owns the whole admin console,
and `main` serves the public site last. No pattern sits at the bare URL root
except the public pages themselves, so an organizer or admin route can never be
shadowed by a tournament slug.

Django's built-in admin is not installed by default and is never routed on a
guessable path.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [
    path('', include('accounts.urls')),        # login/signup doors, profiles, notifications
    path('', include('tournaments.urls')),     # /organizer/…, /player/join/…, live-score API
    path('', include('support.urls')),         # /player/support, /organizer/support
    path('dashboard/', include('dash.urls')),  # the platform-admin application
    path('', include('main.urls')),            # public: home, browse, tournaments, matches
]

handler403 = 'main.errors.permission_denied'
handler404 = 'main.errors.page_not_found'
handler500 = 'main.errors.server_error'

# Developer-only: Django's built-in admin. OFF by default; when a developer turns
# it on it mounts at an env-controlled, non-guessable path. Public users, players,
# organizers and platform admins never see it — the admin console at /dashboard/
# is the only administrative surface this product has.
if settings.ENABLE_DJANGO_ADMIN:
    from django.contrib import admin
    urlpatterns.insert(0, path(settings.DJANGO_ADMIN_URL, admin.site.urls))

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
