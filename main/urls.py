"""Public audience routes + the player dashboard.

Nothing above the player block requires authentication. The tournament/match
slug patterns are namespaced under `/t/` so the URL root stays free — no public
page can ever shadow an organizer or admin route.
"""
from django.urls import path

from . import views

urlpatterns = [
    # --- Public: discovery ---
    path('', views.home, name='home'),
    path('browse', views.browse, name='browse'),
    path('search', views.search, name='search'),
    path('live', views.live_list, name='live_list'),
    path('results', views.results_list, name='results_list'),
    path('highlights', views.highlights_list, name='highlights_list'),
    path('sports/<slug:slug>', views.sport_detail, name='sport_detail'),
    path('teams', views.teams_list, name='teams_list'),
    path('teams/<int:pk>', views.team_detail, name='team_detail'),
    path('players', views.players_list, name='players_list'),
    path('news', views.news_list, name='news_list'),
    path('news/<slug:slug>', views.news_detail, name='news_detail'),

    # --- Public: tournament + match ---
    path('t/<slug:slug>', views.tournament_detail, name='tournament_detail'),
    path('t/<slug:slug>/standings', views.standings, name='tournament_standings'),
    path('t/<slug:slug>/schedule', views.schedule, name='tournament_schedule'),
    path('t/<slug:slug>/m/<int:pk>', views.match_detail, name='match_detail'),


    # --- Player dashboard (authenticated) ---
    path('player/', views.player_dashboard, name='player_dashboard'),
    path('referee/', views.referee_dashboard, name='referee_dashboard'),
    path('commentator/', views.commentator_dashboard, name='commentator_dashboard'),

    path('player/tournaments', views.my_tournaments, name='my_tournaments'),
    path('player/schedule', views.my_schedule, name='my_schedule'),
    path('player/results', views.my_results, name='my_results'),
]
