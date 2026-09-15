"""The public audience site (no authentication anywhere) + the player dashboard.

Everything above `_PLAYER AREA_` is open to anonymous visitors: homepage,
browse, sports, tournaments, matches, teams, players, standings, schedules,
highlights, results, live scores and news. No view here calls a login guard.
"""
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from accounts.decorators import player_required
from accounts.models import Follow, PlayerProfile
from dash.models import News
from tournaments import constants as C
from tournaments.models import (Fixture, Highlight, ScoreEvent, Sport, Team, Tournament,
                                TournamentTeamEntry)
from tournaments.stats import player_results, player_schedule
from tournaments.utils import format_ms, youtube_id
from tournaments.views import _basketball_scoring_context


# ======================================================================
# Shared helpers
# ======================================================================
def _live_fixtures(limit=12):
    return list(
        Fixture.objects.filter(status='LIVE', is_removed=False)
        .exclude(tournament__status__in=['DRAFT', 'CANCELLED'])
        .filter(tournament__is_removed=False)
        .select_related('tournament__sport')
        .prefetch_related('participants__team', 'participants__player__user')[:limit])


# ======================================================================
# Public — audience (no login, ever)
# ======================================================================
def home(request):
    public = Tournament.objects.public().select_related('sport', 'organizer__user')
    live_fx = _live_fixtures()
    # Hero card prefers a head-to-head (2-competitor) live match.
    hero_fx = next((f for f in live_fx if f.participants.count() == 2), None) or (
        live_fx[0] if live_fx else None)
    sports = Sport.objects.annotate(
        n_live=Count('tournaments', filter=Q(tournaments__status='ONGOING',
                                             tournaments__is_removed=False)),
        n_total=Count('tournaments', filter=~Q(tournaments__status='DRAFT') &
                      Q(tournaments__is_removed=False)),
    )
    return render(request, 'public/home.html', {
        'featured': public.filter(is_featured=True).order_by('featured_order', '-created_at')[:5],
        'live': public.filter(status='ONGOING')[:6],
        'upcoming': public.filter(status='PUBLISHED').order_by('start_date')[:6],
        'sports': sports,
        'live_fixtures': live_fx,
        'hero_fixture': hero_fx,
        'highlights': Highlight.objects.filter(
            published_at__isnull=False, is_removed=False)
            .select_related('tournament__sport').exclude(tournament__status='DRAFT')[:4],
        'news': News.objects.published().select_related('sport')[:3],
        'stats': {
            'tournaments': Tournament.objects.exclude(status='DRAFT').filter(is_removed=False).count(),
            'players': PlayerProfile.objects.filter(user__is_suspended=False).count(),
            'sports': Sport.objects.count(),
        },
    })


def browse(request):
    qs = Tournament.objects.public().select_related('sport', 'organizer__user')
    sport_slugs = request.GET.getlist('sport')
    city = request.GET.get('city', '').strip()
    status = request.GET.get('status', '').strip()
    date_from = request.GET.get('from', '').strip()
    date_to = request.GET.get('to', '').strip()

    if sport_slugs:
        qs = qs.filter(sport__slug__in=sport_slugs)
    if city:
        qs = qs.filter(city__icontains=city)
    if status:
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(start_date__gte=date_from)
    if date_to:
        qs = qs.filter(start_date__lte=date_to)

    page = Paginator(qs.order_by('-created_at'), 12).get_page(request.GET.get('page'))

    # Carry every active filter into the page links. Building this from the real
    # query string (minus `page`) means a new filter can never be silently
    # dropped on page 2 — which is exactly what used to happen.
    params = request.GET.copy()
    params.pop('page', None)

    return render(request, 'public/browse.html', {
        'page': page,
        'sports': Sport.objects.all(),
        'selected_sports': sport_slugs,
        'filters': {'city': city, 'status': status, 'from': date_from, 'to': date_to},
        'querystring': params.urlencode(),
        # Draft is never a public filter option.
        'status_choices': [c for c in C.TOURNAMENT_STATUS if c[0] != 'DRAFT'],
    })


def sport_detail(request, slug):
    sport = get_object_or_404(Sport, slug=slug)
    tournaments = Tournament.objects.public().filter(sport=sport).select_related('organizer__user')
    return render(request, 'public/sport_detail.html', {
        'sport': sport,
        'tournaments': tournaments,
        'live_fixtures': [f for f in _live_fixtures(24) if f.tournament.sport_id == sport.id][:6],
    })


def teams_list(request):
    """Public team directory."""
    q = request.GET.get('q', '').strip()
    sport_slug = request.GET.get('sport', '').strip()
    qs = Team.objects.select_related('sport').order_by('name')
    if q:
        qs = qs.filter(name__icontains=q)
    if sport_slug:
        qs = qs.filter(sport__slug=sport_slug)
    page = Paginator(qs, 24).get_page(request.GET.get('page'))
    return render(request, 'public/teams.html', {
        'page': page, 'q': q, 'sports': Sport.objects.all(), 'sport_slug': sport_slug})


def team_detail(request, pk):
    """Public team page: roster, tournaments entered, recent results."""
    team = get_object_or_404(Team.objects.select_related('sport'), pk=pk)
    entries = TournamentTeamEntry.objects.filter(team=team).select_related(
        'tournament__sport').exclude(tournament__status='DRAFT').filter(
        tournament__is_removed=False)
    fixtures = Fixture.objects.filter(
        participants__team=team, is_removed=False, tournament__is_removed=False
    ).exclude(tournament__status='DRAFT').select_related('tournament__sport').prefetch_related(
        'participants__team', 'participants__player__user').distinct().order_by(
        '-scheduled_time', '-id')[:20]
    return render(request, 'public/team_detail.html', {
        'team': team,
        'roster': team.memberships.select_related('player__user'),
        'entries': entries,
        'fixtures': fixtures,
    })


def players_list(request):
    """Public player directory."""
    q = request.GET.get('q', '').strip()
    qs = PlayerProfile.objects.select_related('user').filter(user__is_suspended=False)
    if q:
        qs = qs.filter(Q(user__first_name__icontains=q) | Q(user__username__icontains=q) |
                       Q(city__icontains=q))
    page = Paginator(qs.order_by('user__first_name'), 24).get_page(request.GET.get('page'))
    return render(request, 'public/players.html', {'page': page, 'q': q})


def _detail_context(tournament):
    """Engine-aware pieces for the tournament detail page."""
    ctx = {
        # Battle-royale lobbies score on placement + kills, so their leaderboard
        # has a different shape from a two-competitor points table.
        'is_lobby': (tournament.format == C.FORMAT_ROUND_ROBIN
                     and tournament.sport.slug == 'mobile-esports'),
        # Only a timed format has bibs and pace to show.
        'is_timed': tournament.format in (C.FORMAT_TIME_TRIAL, C.FORMAT_SINGLE_EVENT),
    }
    fixtures = tournament.fixtures.filter(is_removed=False).prefetch_related(
        'participants__team', 'participants__player__user').select_related('event_category')
    # Ordered so {% regroup fixtures by round_name %} (the Fixtures tab's
    # generic fallback grouping) sees each round's fixtures contiguously —
    # regroup silently fragments a group if it isn't already sorted.
    ctx['fixtures'] = fixtures.order_by('round_no', 'sequence')
    if tournament.is_pool_stage:
        # Pool Stage + Knockout brings its own pool tables, pool-grouped
        # fixtures and bracket — see tournaments/pools.py. Every other
        # tournament falls through to the format branches below, unchanged.
        from tournaments.pools import pool_view_context
        ctx['is_pool_stage'] = True
        ctx.update(pool_view_context(tournament))
        ctx['bracket_rounds'] = ctx.get('knockout_rounds') or []
        return ctx
    if tournament.format == C.FORMAT_KNOCKOUT:
        rounds = {}
        for fx in fixtures.order_by('round_no', 'sequence'):
            rounds.setdefault(fx.round_no, {'name': fx.round_name, 'fixtures': []})
            rounds[fx.round_no]['fixtures'].append(fx)
        ctx['bracket_rounds'] = [rounds[k] for k in sorted(rounds)]
        # The champion is the winner of the last round — only once it is decided.
        if rounds:
            final = rounds[max(rounds)]['fixtures']
            if len(final) == 1 and final[0].status == 'COMPLETED':
                ctx['champion'] = next(
                    (p for p in final[0].participants.all() if p.is_winner), None)
    elif tournament.format == C.FORMAT_ROUND_ROBIN:
        standings = list(tournament.standings.select_related('team', 'player__user').all())
        ctx['standings'] = standings
        # Only show a rating column when ratings actually exist.
        ctx['has_ratings'] = any((s.extra_stats or {}).get('rating') for s in standings)
    else:  # time-trial / single-event -> leaderboard from the last session
        last = fixtures.order_by('-session_no', '-sequence').first()
        if last:
            ctx['leaderboard'] = last.ordered_participants()
            ctx['leaderboard_fixture'] = last
    return ctx


def _goals_table_context(tournament):
    """Tournament-wide individual scoring leaderboard: rolls up the same
    per-fixture ScoreEvent rows the live-scoring view records (event_type=
    'score', see tournaments.views._basketball_scoring_context) across every
    fixture in this tournament — no separate scoring system, just a wider
    aggregate of the existing one.

    Team-based only: jersey numbers and rosters live on TeamMembership, which
    only exists for team-based sports, so an individual-registration
    tournament has no "team/jersey" shape to show here. Returns None for
    those (hides the tab); returns a list — possibly empty — otherwise.
    """
    if not tournament.is_team_based:
        return None
    totals = {}
    events = ScoreEvent.objects.filter(
        fixture__tournament=tournament, fixture__is_removed=False, event_type='score'
    ).select_related('participant__team')
    for ev in events:
        snap = ev.score_snapshot or {}
        mid = snap.get('membership_id')
        pts = snap.get('points')
        if not mid or not isinstance(pts, (int, float)):
            continue
        row = totals.setdefault(mid, {
            'name': snap.get('player_name', ''), 'jersey_number': snap.get('jersey_number', ''),
            'team_name': ev.participant.name if ev.participant else '', 'total': 0,
        })
        row['total'] += pts
    return sorted(totals.values(), key=lambda r: (-r['total'], r['name'].lower()))


def tournament_detail(request, slug):
    tournament = get_object_or_404(
        Tournament.objects.select_related('sport', 'organizer__user', 'venue'), slug=slug)
    # A draft or admin-removed tournament is visible only to its owner and admins.
    if tournament.status == 'DRAFT' or tournament.is_removed:
        if not request.user.is_authenticated or (
                tournament.organizer.user_id != request.user.id and not request.user.is_staff):
            from django.http import Http404
            raise Http404()

    ctx = {'tournament': tournament, 'yt_id': youtube_id(tournament.youtube_url)}
    ctx.update(_detail_context(tournament))
    ctx['goals_table'] = _goals_table_context(tournament)
    if tournament.is_team_based:
        ctx['entries'] = tournament.team_entries.filter(status='APPROVED').select_related('team')
    else:
        ctx['entries'] = tournament.registrations.filter(status='APPROVED').select_related('player__user')
    ctx['highlights'] = tournament.highlights.filter(published_at__isnull=False, is_removed=False)
    ctx['is_owner'] = (request.user.is_authenticated and not request.user.is_staff and
                       tournament.organizer.user_id == request.user.id)
    ctx['can_join'] = (
        tournament.status in ('PUBLISHED', 'ONGOING')
        and not (tournament.registration_deadline
                 and timezone.now() > tournament.registration_deadline))
    ctx['is_following'] = (
        request.user.is_authenticated and not request.user.is_staff and
        Follow.objects.filter(user=request.user, tournament=tournament).exists())
    ctx['follower_count'] = tournament.followers.count()
    return render(request, 'public/tournament_detail.html', ctx)


def match_detail(request, slug, pk):
    fixture = get_object_or_404(
        Fixture.objects.select_related('tournament__sport', 'event_category'),
        pk=pk, tournament__slug=slug, is_removed=False)
    tournament = fixture.tournament
    if tournament.status == 'DRAFT' or tournament.is_removed:
        if not request.user.is_authenticated or (
                tournament.organizer.user_id != request.user.id and not request.user.is_staff):
            from django.http import Http404
            raise Http404()
    highlight = fixture.highlights.filter(published_at__isnull=False, is_removed=False).first()
    if highlight:
        # F() so concurrent viewers can't clobber each other's increment.
        Highlight.objects.filter(pk=highlight.pk).update(view_count=F('view_count') + 1)
        highlight.refresh_from_db(fields=['view_count'])

    is_basketball = tournament.sport.slug == 'basketball'
    ctx = {
        'tournament': tournament,
        'fixture': fixture,
        'participants': fixture.ordered_participants(),
        'highlight': highlight,
        'events': fixture.events.all()[:30],
        'is_time': tournament.format in (C.FORMAT_TIME_TRIAL, C.FORMAT_SINGLE_EVENT),
        'is_lobby': (tournament.format == C.FORMAT_ROUND_ROBIN
                     and tournament.sport.slug == 'mobile-esports'),
        # Drives the same quarter-clock markup/JS the organizer scoring page
        # uses (see template/organizer/score.html + static/js/match-clock.js)
        # — basketball only, same as that page.
        'is_basketball': is_basketball,
        'win_probability': fixture.win_probability,
        'format_ms': format_ms,
    }
    if is_basketball and fixture.status != 'SCHEDULED':
        # Same helper the organizer's own Individual Scoring panel uses
        # (tournaments/views.py) — one source of truth for the per-player
        # totals, just rendered read-only here (see
        # template/public/_individual_scoring.html). Not shown pre-match,
        # same as the quarter/shot clock above.
        _, _, individual_rows_by_team = _basketball_scoring_context(fixture)
        ctx['individual_rows_by_team'] = individual_rows_by_team
    return render(request, 'public/match_detail.html', ctx)


def standings(request, slug):
    """Public standings / leaderboard page for one tournament."""
    tournament = get_object_or_404(Tournament.objects.public().select_related('sport'), slug=slug)
    ctx = {'tournament': tournament}
    ctx.update(_detail_context(tournament))
    return render(request, 'public/standings.html', ctx)


def schedule(request, slug):
    """Public schedule page for one tournament."""
    tournament = get_object_or_404(Tournament.objects.public().select_related('sport'), slug=slug)
    fixtures = tournament.fixtures.filter(is_removed=False).prefetch_related(
        'participants__team', 'participants__player__user').order_by(
        'scheduled_time', 'round_no', 'sequence')
    return render(request, 'public/schedule.html', {'tournament': tournament, 'fixtures': fixtures})


def highlights_list(request):
    """Public highlight reel across every tournament."""
    qs = Highlight.objects.filter(published_at__isnull=False, is_removed=False).select_related(
        'tournament__sport', 'fixture').exclude(tournament__status='DRAFT').filter(
        tournament__is_removed=False)
    page = Paginator(qs, 12).get_page(request.GET.get('page'))
    return render(request, 'public/highlights.html', {'page': page})


def results_list(request):
    """Public results feed — every completed match, newest first."""
    qs = Fixture.objects.filter(status='COMPLETED', is_removed=False,
                                tournament__is_removed=False, result_published=True
                                ).exclude(tournament__status='DRAFT').select_related(
        'tournament__sport').prefetch_related('participants__team', 'participants__player__user'
                                              ).order_by('-updated_at')
    page = Paginator(qs, 20).get_page(request.GET.get('page'))
    return render(request, 'public/results.html', {'page': page})


def live_list(request):
    """Public live-score board."""
    return render(request, 'public/live.html', {'fixtures': _live_fixtures(30)})


def news_list(request):
    page = Paginator(News.objects.published().select_related('sport'), 9).get_page(
        request.GET.get('page'))
    return render(request, 'public/news_list.html', {'page': page})


def news_detail(request, slug):
    article = get_object_or_404(News.objects.published().select_related('sport', 'tournament'),
                                slug=slug)
    return render(request, 'public/news_detail.html', {
        'article': article,
        'more': News.objects.published().exclude(pk=article.pk)[:3],
    })


def search(request):
    q = request.GET.get('q', '').strip()
    tournaments = teams = players = []
    if q:
        tournaments = Tournament.objects.public().select_related('sport').filter(
            Q(name__icontains=q) | Q(city__icontains=q))[:20]
        teams = Team.objects.select_related('sport').filter(name__icontains=q)[:20]
        players = PlayerProfile.objects.select_related('user').filter(
            Q(user__first_name__icontains=q) | Q(user__username__icontains=q),
            user__is_suspended=False)[:20]
    return render(request, 'public/search.html',
                  {'q': q, 'tournaments': tournaments, 'teams': teams, 'players': players})


# ======================================================================
# PLAYER AREA — authenticated, isolated from organizer and admin
# ======================================================================
@player_required
def player_dashboard(request):
    profile, _ = PlayerProfile.objects.get_or_create(user=request.user)
    joined = Tournament.objects.filter(
        Q(registrations__player=profile) |
        Q(team_entries__team__memberships__player=profile)
    ).distinct().select_related('sport')
    return render(request, 'players/dashboard.html', {
        'profile': profile,
        'schedule': player_schedule(profile)[:5],
        'results': player_results(profile)[:5],
        'joined_count': joined.count(),
        'following_count': Follow.objects.filter(user=request.user).count(),
        'upcoming': Tournament.objects.public().filter(status='PUBLISHED').select_related(
            'sport').order_by('start_date')[:4],
    })


@player_required
def my_tournaments(request):
    profile, _ = PlayerProfile.objects.get_or_create(user=request.user)
    joined = Tournament.objects.filter(
        Q(registrations__player=profile) |
        Q(team_entries__team__memberships__player=profile)
    ).distinct().select_related('sport', 'organizer__user').order_by('-start_date')
    return render(request, 'players/tournaments.html', {'tournaments': joined})


@player_required
def my_schedule(request):
    profile, _ = PlayerProfile.objects.get_or_create(user=request.user)
    return render(request, 'players/schedule.html',
                  {'fixtures': player_schedule(profile), 'title': 'My Schedule'})


@player_required
def my_results(request):
    profile, _ = PlayerProfile.objects.get_or_create(user=request.user)
    return render(request, 'players/results.html',
                  {'fixtures': player_results(profile), 'title': 'My Results'})
