"""Organizer management routes + the public live-score API.

Everything an organizer can do is namespaced under `/organizer/`. Previously
these patterns sat at the URL root (`/<slug>/manage`), which made the root a
greedy catch-all and put organizer tools one guessed path away from every public
page. Player participation lives under `/player/`.
"""
from django.urls import path

from . import views
from . import team_views
from . import registration_views
from . import referee_views
from . import co_organizer_views
from . import commentator_views
from . import commentary_views

urlpatterns = [
    # --- Public live-score JSON (polled by audience pages, no auth) ---
    path('api/fixtures/<int:fixture_id>/live', views.fixture_live_json, name='fixture_live_json'),

    # --- Player participation ---
    path('player/join/<slug:slug>', registration_views.tournament_join_dispatch, name='tournament_join'),
    path('player/apply-to-team/<slug:slug>', registration_views.apply_to_team, name='apply_to_team'),
    path('player/follow/<slug:slug>', views.tournament_follow, name='tournament_follow'),
    path('player/following', views.my_following, name='my_following'),
    path('api/search_partner', registration_views.api_search_partner, name='api_search_partner'),
    path('api/search_users', team_views.api_search_users, name='api_search_users'),

    
    # --- Player Team Management ---
    path('my-teams/', team_views.my_teams, name='my_teams'),
    
    path('teams/<int:team_id>/manage', team_views.team_manage, name='team_manage'),
    path('teams/<int:team_id>/member/<int:membership_id>/status', team_views.team_member_status, name='team_member_status'),
    path('teams/<int:team_id>/member/<int:membership_id>/role', team_views.team_member_role, name='team_member_role'),
    path('teams/<int:team_id>/member/<int:membership_id>/remove', team_views.team_member_remove, name='team_member_remove'),
    path('teams/<int:team_id>/request/<int:request_id>', team_views.team_join_request_respond, name='team_join_request_respond'),
    path('teams/<int:team_id>/member/add', team_views.team_add_member, name='team_add_member'),

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
    path('organizer/t/<slug:slug>/entry/<int:entry_id>/approve', registration_views.entry_approve, name='entry_approve'),
    path('organizer/t/<slug:slug>/entry/<int:entry_id>/reject', registration_views.entry_reject, name='entry_reject'),
    path('organizer/t/<slug:slug>/individual/<int:reg_id>/approve', registration_views.individual_approve, name='individual_approve'),
    path('organizer/t/<slug:slug>/individual/<int:reg_id>/reject', registration_views.individual_reject, name='individual_reject'),
    path('organizer/t/<slug:slug>/participants/team/add', views.team_add, name='team_add'),
    path('organizer/t/<slug:slug>/participants/team/import', views.teams_bulk_import, name='teams_bulk_import'),
    path('organizer/t/<slug:slug>/participants/team/<int:team_id>/member/add',
         views.team_member_add, name='team_member_add'),
    path('organizer/t/<slug:slug>/participants/team/<int:team_id>/member/import',
         views.team_members_bulk_import, name='team_members_bulk_import'),
    # Renames must be registered before entry_decide's <str:kind>/<int:entry_id>/<str:decision>
    # catch-all below, which would otherwise swallow /team/<id>/rename and
    # /individual/<id>/rename first (both match its 3-segment shape).
    path('organizer/t/<slug:slug>/participants/team/<int:team_id>/edit',
         views.team_edit, name='team_edit'),
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
    path('organizer/t/<slug:slug>/participants/individual/import',
         views.individual_bulk_import, name='individual_bulk_import'),
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
    path('organizer/t/<slug:slug>/fixtures/chess-export', views.chess_results_export,
         name='chess_results_export'),
    path('organizer/t/<slug:slug>/categories/create', views.category_create, name='category_create'),
    path('organizer/t/<slug:slug>/categories/delete', views.category_delete, name='category_delete'),
    path('organizer/t/<slug:slug>/fixtures/set-category', views.fixture_set_category, name='fixture_set_category'),
    path('organizer/participants/search', views.participant_search, name='participant_search'),
    path('organizer/t/<slug:slug>/fixtures/pools/add-fixture', views.pool_fixture_add,
         name='pool_fixture_add'),
    path('organizer/t/<slug:slug>/fixtures/pools/build', views.pool_fixture_build,
         name='pool_fixture_build'),
    path('organizer/t/<slug:slug>/fixtures/pools/create', views.pool_create, name='pool_create'),
    path('organizer/t/<slug:slug>/fixtures/pools/rename', views.pool_rename, name='pool_rename'),
    path('organizer/t/<slug:slug>/fixtures/pools/reorder', views.pool_reorder, name='pool_reorder'),
    path('organizer/t/<slug:slug>/fixtures/pools/knockout/rename', views.knockout_round_rename,
         name='knockout_round_rename'),
    path('organizer/t/<slug:slug>/fixtures/pools/knockout/add-fixture', views.knockout_round_fixture_add,
         name='knockout_round_fixture_add'),
    path('organizer/t/<slug:slug>/fixtures/clear-all', views.fixtures_clear_all, name='fixtures_clear_all'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/delete',
         views.fixture_delete, name='fixture_delete'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/schedule',
         views.fixture_schedule, name='fixture_schedule'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/score',
         views.score_fixture, name='score_fixture'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/highlight',
         views.highlight_manage, name='highlight_manage'),

    # --- Co-Organizer Management ---
    path('organizer/t/<slug:slug>/co-organizers/invite',
         co_organizer_views.tournament_co_organizer_invite, name='tournament_co_organizer_invite'),
    path('organizer/t/<slug:slug>/co-organizers/<int:user_id>/revoke',
         co_organizer_views.tournament_co_organizer_revoke, name='tournament_co_organizer_revoke'),
    path('organizer/t/<slug:slug>/co-organizers/invitations/<int:invite_id>/cancel',
         co_organizer_views.tournament_co_organizer_cancel_invite, name='tournament_co_organizer_cancel_invite'),
    path('co-organizers/accept/<str:token>',
         co_organizer_views.co_organizer_accept, name='co_organizer_accept'),

    # --- Tournament Referee Management ---
    path('organizer/t/<slug:slug>/referees/search-api',
         referee_views.tournament_referees_search_api, name='tournament_referees_search_api'),
    path('organizer/t/<slug:slug>/referees/add',
         referee_views.tournament_referee_add, name='tournament_referee_add'),
    path('organizer/t/<slug:slug>/referees/<int:user_id>/remove',
         referee_views.tournament_referee_remove, name='tournament_referee_remove'),
    path('organizer/t/<slug:slug>/referees/auto-assign',
         referee_views.tournament_referees_auto_assign, name='tournament_referees_auto_assign'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/referees/assign',
         referee_views.fixture_referee_assign, name='fixture_referee_assign'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/referees/<int:user_id>/unassign',
         referee_views.fixture_referee_unassign, name='fixture_referee_unassign'),

    # --- Tournament Commentator Management ---
    path('organizer/t/<slug:slug>/commentators/search-api',
         commentator_views.tournament_commentators_search_api, name='tournament_commentators_search_api'),
    path('organizer/t/<slug:slug>/commentators/add',
         commentator_views.tournament_commentator_add, name='tournament_commentator_add'),
    path('organizer/t/<slug:slug>/commentators/<int:user_id>/remove',
         commentator_views.tournament_commentator_remove, name='tournament_commentator_remove'),
    path('organizer/t/<slug:slug>/commentators/auto-assign',
         commentator_views.tournament_commentators_auto_assign, name='tournament_commentators_auto_assign'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/commentators/assign',
         commentator_views.fixture_commentator_assign, name='fixture_commentator_assign'),
    path('organizer/t/<slug:slug>/fixtures/<int:fixture_id>/commentators/<int:user_id>/unassign',
         commentator_views.fixture_commentator_unassign, name='fixture_commentator_unassign'),

    # --- Live Commentary Workspace & Real-Time Sync ---
    path('t/<slug:slug>/fixtures/<int:fixture_id>/commentary',
         commentary_views.fixture_commentary_workspace, name='fixture_commentary_workspace'),
    path('api/t/<slug:slug>/fixtures/<int:fixture_id>/commentary/publish',
         commentary_views.commentary_publish_api, name='commentary_publish_api'),
    path('api/t/<slug:slug>/fixtures/<int:fixture_id>/commentary/<int:entry_id>/edit',
         commentary_views.commentary_edit_api, name='commentary_edit_api'),
    path('api/t/<slug:slug>/fixtures/<int:fixture_id>/commentary/<int:entry_id>/delete',
         commentary_views.commentary_delete_api, name='commentary_delete_api'),
    path('api/t/<slug:slug>/fixtures/<int:fixture_id>/commentary/sync',
         commentary_views.commentary_sync_api, name='commentary_sync_api'),
    path('api/t/<slug:slug>/fixtures/<int:fixture_id>/commentary/stream',
         commentary_views.commentary_stream_api, name='commentary_stream_api'),
]
