"""Organizer management routes + the public live-score API.

Everything an organizer can do is namespaced under `/organizer/`. Previously
these patterns sat at the URL root (`/<slug>/manage`), which made the root a
greedy catch-all and put organizer tools one guessed path away from every public
page. Player participation lives under `/player/`.
"""
from django.urls import path

from . import views

urlpatterns = [
    # --- Public live-score JSON (polled by audience pages, no auth) ---
    path('api/fixtures/<int:fixture_id>/live', views.fixture_live_json, name='fixture_live_json'),

    # --- Player participation ---
    path('player/join/<slug:slug>', views.tournament_join, name='tournament_join'),
    path('player/follow/<slug:slug>', views.tournament_follow, name='tournament_follow'),
    path('player/following', views.my_following, name='my_following'),

    # --- Organizer dashboard ---
    path('organizer/', views.organizer_dashboard, name='organizer_dashboard'),
    path('organizer/tournaments/new', views.tournament_create, name='tournament_create'),

    # --- Per-tournament organizer management ---
    path('organizer/t/<slug:slug>/', views.tournament_manage, name='tournament_manage'),
    path('organizer/t/<slug:slug>/edit', views.tournament_edit, name='tournament_edit'),
    path('organizer/t/<slug:slug>/publish', views.tournament_publish, name='tournament_publish'),
    path('organizer/t/<slug:slug>/cancel', views.tournament_cancel, name='tournament_cancel'),
    path('organizer/t/<slug:slug>/delete', views.tournament_delete, name='tournament_delete'),

    # Participants / teams / registrations
    path('organizer/t/<slug:slug>/participants', views.participants_manage, name='participants_manage'),
    path('organizer/t/<slug:slug>/participants/team/add', views.team_add, name='team_add'),
    path('organizer/t/<slug:slug>/participants/team/import', views.teams_bulk_import, name='teams_bulk_import'),
    path('organizer/t/<slug:slug>/participants/team/<int:team_id>/member/add',
         views.team_member_add, name='team_member_add'),
    path('organizer/t/<slug:slug>/participants/team/<int:team_id>/member/import',
         views.team_members_bulk_import, name='team_members_bulk_import'),
    # Renames must be registered before entry_decide's <str:kind>/<int:entry_id>/<str:decision>
    # catch-all below, which would otherwise swallow /team/<id>/rename and
    # /individual/<id>/rename first (both match its 3-segment shape).
    path('organizer/t/<slug:slug>/participants/team/<int:team_id>/rename',
         views.team_rename, name='team_rename'),
    path('organizer/t/<slug:slug>/participants/team/<int:team_id>/member/<int:membership_id>/rename',
         views.team_member_rename, name='team_member_rename'),
    path('organizer/t/<slug:slug>/participants/individual/<int:reg_id>/rename',
         views.registration_rename, name='registration_rename'),
    path('organizer/t/<slug:slug>/participants/remove-all',
         views.participants_remove_all, name='participants_remove_all'),
    path('organizer/t/<slug:slug>/participants/import',
         views.participants_bulk_import, name='participants_bulk_import'),
    path('organizer/t/<slug:slug>/participants/member/<int:membership_id>/<str:decision>',
         views.member_decide, name='member_decide'),
    path('organizer/t/<slug:slug>/participants/individual/add',
         views.individual_add, name='individual_add'),
    path('organizer/t/<slug:slug>/participants/<str:kind>/<int:entry_id>/<str:decision>',
         views.entry_decide, name='entry_decide'),

    # Fixtures & scheduling
    path('organizer/t/<slug:slug>/fixtures', views.fixtures_manage, name='fixtures_manage'),
    path('organizer/t/<slug:slug>/fixtures/generate', views.fixtures_generate, name='fixtures_generate'),
    path('organizer/t/<slug:slug>/fixtures/generate-bracket', views.fixtures_generate_bracket,
         name='fixtures_generate_bracket'),
    path('organizer/t/<slug:slug>/fixtures/bracket/seed', views.bracket_seed_set, name='bracket_seed_set'),
    path('organizer/t/<slug:slug>/fixtures/add-manual', views.fixture_add_manual, name='fixture_add_manual'),
    # Pool Stage + Knockout (basketball only) — additive to the routes above.
    path('organizer/t/<slug:slug>/fixtures/mode', views.fixture_mode_set, name='fixture_mode_set'),
    path('organizer/t/<slug:slug>/fixtures/pools', views.pool_setup, name='pool_setup'),
    path('organizer/t/<slug:slug>/fixtures/pools/knockout', views.pool_knockout_generate,
         name='pool_knockout_generate'),
    # Swiss format (chess) — additive to the routes above.
    path('organizer/t/<slug:slug>/fixtures/swiss', views.swiss_setup, name='swiss_setup'),
    path('organizer/t/<slug:slug>/fixtures/swiss/next-round', views.swiss_generate_round,
         name='swiss_generate_round'),
    path('organizer/t/<slug:slug>/fixtures/pools/add-fixture', views.pool_fixture_add,
         name='pool_fixture_add'),
    path('organizer/t/<slug:slug>/fixtures/pools/build', views.pool_fixture_build,
         name='pool_fixture_build'),
    path('organizer/t/<slug:slug>/fixtures/pools/create', views.pool_create, name='pool_create'),
    path('organizer/t/<slug:slug>/fixtures/pools/rename', views.pool_rename, name='pool_rename'),
    path('organizer/t/<slug:slug>/fixtures/clear-all', views.fixtures_clear_all, name='fixtures_clear_all'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/delete',
         views.fixture_delete, name='fixture_delete'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/schedule',
         views.fixture_schedule, name='fixture_schedule'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/score',
         views.score_fixture, name='score_fixture'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/highlight',
         views.highlight_manage, name='highlight_manage'),
]
