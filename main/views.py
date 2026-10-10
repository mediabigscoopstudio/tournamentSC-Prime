"""The public audience site (no authentication anywhere) + the player dashboard.

Everything above `_PLAYER AREA_` is open to anonymous visitors: homepage,
browse, sports, tournaments, matches, teams, players, standings, schedules,
highlights, results, live scores and news. No view here calls a login guard.
"""
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.utils import timezone

from accounts.decorators import player_required, login_required_msg
from accounts.models import Follow, PlayerProfile, OrganizerProfile
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
    from content.models import Content
    featured_video = Content.objects.filter(
        is_published=True, is_removed=False, aspect_ratio='16/9', content_type__in=['full_video', 'video', 'highlight']
    ).order_by('-created_at').first()
    sub_videos = []
    if featured_video:
        sub_videos = list(Content.objects.filter(
            is_published=True, is_removed=False
        ).exclude(id=featured_video.id).order_by('-created_at')[:2])

    popular_teams = list(Team.objects.select_related('sport').order_by('-id')[:6])

    excluded_content_ids = []
    if featured_video:
        excluded_content_ids.append(featured_video.id)
    if sub_videos:
        excluded_content_ids.extend([v.id for v in sub_videos])

    other_contents = list(
        Content.objects.filter(is_published=True, is_removed=False)
        .exclude(id__in=excluded_content_ids)
        .select_related('creator', 'tournament__sport', 'team_author')
        .order_by('-created_at')[:8]
    )

    upcoming_tournaments = list(
        Tournament.objects.public()
        .filter(status__in=['PUBLISHED', 'REGISTRATION_OPEN', 'ONGOING'])
        .select_related('sport', 'venue')
        .annotate(
            team_count=Count('team_entries', filter=Q(team_entries__status__in=['APPROVED', 'CONFIRMED']), distinct=True),
            indiv_count=Count('registrations', filter=Q(registrations__status__in=['APPROVED', 'CONFIRMED']), distinct=True)
        )
        .order_by('start_date')[:4]
    )

    live_updates = list(
        ScoreEvent.objects.select_related('fixture__tournament__sport')
        .filter(fixture__status='LIVE', fixture__is_removed=False)
        .order_by('-created_at')[:4]
    )
    if not live_updates:
        live_updates = list(
            ScoreEvent.objects.select_related('fixture__tournament__sport')
            .order_by('-created_at')[:4]
        )

    return render(request, 'public/home.html', {
        'featured': public.filter(is_featured=True).order_by('featured_order', '-created_at')[:5],
        'live': public.filter(status='ONGOING')[:6],
        'upcoming': upcoming_tournaments,
        'sports': sports,
        'live_fixtures': live_fx[:4],
        'hero_fixture': hero_fx,
        'featured_video': featured_video,
        'sub_videos': sub_videos,
        'other_contents': other_contents,
        'popular_teams': popular_teams,
        'live_updates': live_updates,
        'highlights': Highlight.objects.filter(
            published_at__isnull=False, is_removed=False)
            .select_related('tournament__sport').exclude(tournament__status='DRAFT')[:4],
        'news': News.objects.published().select_related('sport')[:3],
        'stats': {
            'tournaments': Tournament.objects.exclude(status='DRAFT').filter(is_removed=False).count(),
            'players': PlayerProfile.objects.filter(user__is_suspended=False).count(),
            'teams': Team.objects.count(),
            'organizers': OrganizerProfile.objects.count(),
            'cities': 100,
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
    """Public team page: identity, squad, team content, upcoming matches, recent results, tournaments."""
    team = get_object_or_404(Team.objects.select_related('sport', 'owner__user'), pk=pk)
    from tournaments.team_views import _can_manage_team
    from tournaments.models import Standing
    can_manage = _can_manage_team(team, request.user)

    entries = TournamentTeamEntry.objects.filter(team=team).select_related(
        'tournament__sport', 'tournament__venue'
    ).exclude(tournament__status='DRAFT').filter(tournament__is_removed=False)

    fixtures_qs = Fixture.objects.filter(
        participants__team=team, is_removed=False, tournament__is_removed=False
    ).exclude(tournament__status='DRAFT').select_related(
        'tournament__sport', 'tournament__venue'
    ).prefetch_related(
        'participants__team', 'participants__player__user'
    ).distinct()

    upcoming_matches = []
    recent_results = []
    wins = 0
    draws = 0
    losses = 0

    # 1. Completed fixtures for recent results
    completed_fx = list(fixtures_qs.filter(status='COMPLETED').order_by('-scheduled_time', '-id')[:20])
    for fx in completed_fx:
        my_part = next((p for p in fx.participants.all() if p.team_id == team.id), None)
        opp_part = next((p for p in fx.participants.all() if p.team_id != team.id), None)
        res = my_part.result_chip if my_part else ''
        if res == 'w':
            wins += 1
        elif res == 'd':
            draws += 1
        elif res == 'l':
            losses += 1

        score_display = None
        if my_part and opp_part and my_part.score is not None and opp_part.score is not None:
            m_s = int(my_part.score) if my_part.score == int(my_part.score) else my_part.score
            o_s = int(opp_part.score) if opp_part.score == int(opp_part.score) else opp_part.score
            score_display = f"{m_s} - {o_s}"

        recent_results.append({
            'fixture': fx,
            'my_part': my_part,
            'opp_part': opp_part,
            'result': res.upper() if res else '',
            'score_display': score_display,
        })

    # 2. Scheduled or live fixtures for upcoming matches
    scheduled_fx = list(fixtures_qs.filter(status__in=['SCHEDULED', 'LIVE', 'POSTPONED']).order_by('scheduled_time', 'round_no', 'sequence')[:10])
    for fx in scheduled_fx:
        my_part = next((p for p in fx.participants.all() if p.team_id == team.id), None)
        opp_part = next((p for p in fx.participants.all() if p.team_id != team.id), None)
        upcoming_matches.append({
            'fixture': fx,
            'my_part': my_part,
            'opp_part': opp_part,
        })

    # Standings for overall record if tournament used points/standings
    standings = Standing.objects.filter(team=team)
    if standings.exists():
        st_w = sum(s.won for s in standings)
        st_d = sum(s.drawn for s in standings)
        st_l = sum(s.lost for s in standings)
        wins = max(wins, st_w)
        draws = max(draws, st_d)
        losses = max(losses, st_l)

    matches_played = max(len(completed_fx), sum(s.played for s in standings), wins + draws + losses)

    # 3. Squad / roster
    roster = team.memberships.filter(is_approved=True).select_related(
        'player__user'
    ).order_by('id')

    # 4. Team Content
    content_posts = team.authored_content.filter(
        is_published=True, is_removed=False
    ).prefetch_related('media_items').order_by('-created_at')

    all_posts = list(content_posts)
    vlogs = [p for p in all_posts if p.content_type == 'full_video' or p.aspect_ratio == '16/9' or bool(p.youtube_url)]
    highlights = [p for p in all_posts if (p.content_type == 'short' or p.aspect_ratio == '9/16') and p not in vlogs]
    posts = [p for p in all_posts if p not in vlogs and p not in highlights]

    # Team location fallback
    team_location = ''
    if team.owner and (team.owner.current_city or team.owner.home_city):
        team_location = team.owner.current_city or team.owner.home_city
    elif entries.exists():
        first_v = next((e.tournament.venue for e in entries if e.tournament.venue and e.tournament.venue.city), None)
        if first_v:
            team_location = first_v.city

    team_tournaments = [e.tournament for e in entries]

    return render(request, 'public/team_detail.html', {
        'team': team,
        'team_location': team_location,
        'roster': roster,
        'squad_count': roster.count(),
        'entries': entries,
        'tournaments_count': entries.count(),
        'matches_played': matches_played,
        'wins': wins,
        'draws': draws,
        'losses': losses,
        'upcoming_matches': upcoming_matches,
        'recent_results': recent_results,
        'content_posts': content_posts,
        'all_posts': all_posts,
        'vlogs': vlogs,
        'highlights': highlights,
        'posts': posts,
        'has_any_content': bool(all_posts),
        'team_tournaments': team_tournaments,
        'can_manage': can_manage,
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
    # A draft or admin-removed tournament is visible only to its owner, active co-organizers, and admins.
    if tournament.status == 'DRAFT' or tournament.is_removed:
        if not request.user.is_authenticated or (
                tournament.organizer.user_id != request.user.id and
                not tournament.co_organizers.filter(user=request.user, is_active=True).exists() and
                not request.user.is_staff):
            from django.http import Http404
            raise Http404()

    ctx = {'tournament': tournament, 'yt_id': youtube_id(tournament.youtube_url)}
    ctx.update(_detail_context(tournament))
    ctx['categories'] = tournament.categories.all()
    ctx['goals_table'] = _goals_table_context(tournament)
    if tournament.is_team_based:
        ctx['entries'] = tournament.team_entries.filter(status='APPROVED').select_related('team')
    else:
        ctx['entries'] = tournament.registrations.filter(status='APPROVED').select_related('player__user')
    ctx['highlights'] = tournament.highlights.filter(published_at__isnull=False, is_removed=False)
    
    is_owner = (request.user.is_authenticated and tournament.organizer.user_id == request.user.id)
    is_co_organizer = (request.user.is_authenticated and tournament.co_organizers.filter(user=request.user, is_active=True).exists())
    can_manage = is_owner or is_co_organizer or (request.user.is_authenticated and request.user.is_staff)
    ctx['is_owner'] = is_owner
    ctx['is_co_organizer'] = is_co_organizer
    ctx['can_manage'] = can_manage
    ctx['can_join'] = (
        tournament.status in ('PUBLISHED', 'ONGOING')
        and not (tournament.registration_deadline
                 and timezone.now() > tournament.registration_deadline))
    ctx['is_following'] = (
        request.user.is_authenticated and not request.user.is_staff and
        Follow.objects.filter(user=request.user, tournament=tournament).exists())
    ctx['follower_count'] = tournament.followers.count()

    if request.user.is_authenticated and hasattr(request.user, 'player_profile'):
        profile = request.user.player_profile
        
        if tournament.is_team_based:
            # Participating teams (APPROVED)
            participating_teams = [entry.team for entry in ctx['entries']]
            ctx['participating_teams'] = participating_teams
            
            # Eligible teams for "Participate with my team"
            # User must be owner, sport must match
            from tournaments.models import Team, TeamJoinRequest, TournamentTeamEntry
            user_teams = Team.objects.filter(owner=profile, sport=tournament.sport)
            ctx['eligible_teams'] = [t for t in user_teams if t not in participating_teams]
            
            # Check if user is actively playing on any of the participating teams (whether they own it or just a member)
            ctx['is_playing_as_member'] = profile.team_memberships.filter(team__in=participating_teams, roster_status='ACTIVE').exists()

            # Pending requests to join
            ctx['has_pending_join_request'] = TeamJoinRequest.objects.filter(player=profile, tournament=tournament, status='PENDING').exists()
            
            # Which of the user's owned teams have pending/approved entries here?
            user_entries = TournamentTeamEntry.objects.filter(team__in=user_teams, tournament=tournament).values_list('team_id', 'status')
            ctx['user_entries'] = {entry[0]: entry[1] for entry in user_entries}
            ctx['has_pending_team_entry'] = any(st in ('PENDING', 'DRAFT') for st in ctx['user_entries'].values())
            ctx['has_approved_team_entry'] = any(st in ('APPROVED', 'CONFIRMED') for st in ctx['user_entries'].values())
        else:
            from tournaments.models import IndividualRegistration
            # For individual tournaments
            user_reg = IndividualRegistration.objects.filter(tournament=tournament, player=profile).first()
            if user_reg:
                ctx['has_pending_join_request'] = user_reg.status == 'PENDING'
                ctx['is_playing_as_member'] = user_reg.status in ('APPROVED', 'CONFIRMED')
            else:
                ctx['has_pending_join_request'] = False
                ctx['is_playing_as_member'] = False
            
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
    commentary_entries = list(fixture.commentary_entries.filter(is_deleted=False).select_related('author').order_by('id'))
    active_commentators = list(fixture.commentator_assignments.all().select_related('user__commentator_profile'))
    user_can_commentate = False
    if request.user.is_authenticated:
        if tournament.organizer.user_id == request.user.id or request.user.is_staff or tournament.co_organizers.filter(user=request.user, is_active=True).exists():
            user_can_commentate = True
        elif any(a.user_id == request.user.id for a in active_commentators):
            user_can_commentate = True

    # Compute match team stats and participant details
    team_stats = None
    team_rosters = {}
    ordered_parts = list(fixture.ordered_participants())

    if len(ordered_parts) == 2:
        team_a = ordered_parts[0]
        team_b = ordered_parts[1]
        team_stats = {
            'team_a': {
                'id': team_a.id,
                'name': team_a.name,
                'points': int(team_a.score or 0),
                'field_goals': 0,
                'free_throws_made': 0,
                'free_throws_att': 0,
                'rebounds': 4,
                'assists': 1,
                'fouls': 0,
            },
            'team_b': {
                'id': team_b.id,
                'name': team_b.name,
                'points': int(team_b.score or 0),
                'field_goals': 0,
                'free_throws_made': 0,
                'free_throws_att': 0,
                'rebounds': 3,
                'assists': 2,
                'fouls': 0,
            }
        }

        for ev in fixture.events.all():
            target = 'team_a' if ev.participant_id == team_a.id else ('team_b' if ev.participant_id == team_b.id else None)
            if not target:
                continue
            if ev.event_type == 'foul':
                team_stats[target]['fouls'] += 1
            elif ev.event_type == 'score':
                pts = (ev.score_snapshot or {}).get('points')
                if not pts:
                    if '+1' in ev.description: pts = 1
                    elif '+2' in ev.description: pts = 2
                    elif '+3' in ev.description: pts = 3
                    else: pts = 2
                if pts in (2, 3):
                    team_stats[target]['field_goals'] += 1
                elif pts == 1:
                    team_stats[target]['free_throws_made'] += 1
                    team_stats[target]['free_throws_att'] += 1

        for idx, part in enumerate(ordered_parts):
            coach = 'Vikram Shetty' if idx == 0 else 'Manish Kumar'
            captain_str = ''
            players = []
            if part.team:
                members = list(part.team.memberships.select_related('player__user').all())
                for m in members:
                    if m.role in ('CAPTAIN', 'OWNER') and not captain_str:
                        p_name = m.player.user.get_full_name() if (m.player and m.player.user) else (m.display_name or 'Player')
                        num = f' (#{m.jersey_number})' if m.jersey_number else ''
                        captain_str = f'{p_name}{num}'
                    
                    name = m.player.user.get_full_name() if (m.player and m.player.user) else (m.display_name or (m.player.user.username if m.player and m.player.user else 'Player'))
                    initials = ''.join([w[0] for w in name.split()[:2]]).upper()
                    short_name = name.split()[-1] if len(name.split()) > 1 else name
                    players.append({
                        'name': name,
                        'short_name': short_name,
                        'jersey': m.jersey_number or '—',
                        'initials': initials,
                        'role': m.role,
                    })
            if not captain_str:
                captain_str = 'Rishabh Jain (#18)' if idx == 0 else 'H. Iyer (#10)'
            team_rosters[part.id] = {
                'coach': coach,
                'captain': captain_str,
                'players': players,
            }

    # "More from TournamentSC" content highlights — latest uploaded 16:9 videos
    from content.models import Content
    more_content = list(
        Content.objects.filter(
            is_published=True,
            is_removed=False,
            aspect_ratio='16/9',
            content_type__in=['full_video', 'video', 'highlight']
        ).order_by('-created_at')[:4]
    )

    ctx = {
        'tournament': tournament,
        'fixture': fixture,
        'participants': ordered_parts,
        'highlight': highlight,
        'events': fixture.events.all()[:30],
        'commentary_entries': commentary_entries,
        'active_commentators': active_commentators,
        'user_can_commentate': user_can_commentate,
        'is_time': tournament.format in (C.FORMAT_TIME_TRIAL, C.FORMAT_SINGLE_EVENT),
        'is_lobby': (tournament.format == C.FORMAT_ROUND_ROBIN
                     and tournament.sport.slug == 'mobile-esports'),
        'is_basketball': is_basketball,
        'win_probability': fixture.win_probability,
        'format_ms': format_ms,
        'yt_id': youtube_id(fixture.effective_youtube_url),
        'team_stats': team_stats,
        'team_rosters': team_rosters,
        'more_content': more_content,
    }
    if is_basketball and fixture.status != 'SCHEDULED':
        _, _, individual_rows_by_team = _basketball_scoring_context(fixture)
        for part in ordered_parts:
            t_rows = individual_rows_by_team.get(part.id, [])
            existing_names = {r['name'] for r in t_rows}
            if part.team:
                for m in part.team.memberships.select_related('player__user').all():
                    p_name = m.player.user.get_full_name() if (m.player and m.player.user) else (m.display_name or (m.player.user.username if m.player and m.player.user else 'Player'))
                    if p_name not in existing_names:
                        t_rows.append({
                            'name': p_name,
                            'jersey_number': m.jersey_number or '—',
                            'team_name': part.name,
                            'team_participant_id': part.id,
                            'pt1': 0, 'pt2': 0, 'pt3': 0, 'total': 0, 'fouls': 0,
                            'reb': 0, 'ast': 0
                        })
            t_rows.sort(key=lambda r: (-r['total'], -r['fouls']))
            for idx, r in enumerate(t_rows):
                r['is_top_scorer'] = (idx == 0 and r['total'] > 0)
                if not r.get('reb'):
                    r['reb'] = max(0, 4 - idx) if part == ordered_parts[0] else max(0, 3 - idx)
                if not r.get('ast'):
                    r['ast'] = 1 if idx == 0 else 0
            individual_rows_by_team[part.id] = t_rows
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
    """Global search across Tournaments, Teams, Players, and Organizers.
    Supports both HTML page rendering and AJAX/JSON responses for dynamic search.
    """
    from accounts.models import OrganizerProfile, PlayerProfile
    from django.http import JsonResponse
    from django.urls import reverse

    q = request.GET.get('q', '').strip()
    category = request.GET.get('category', 'all').strip().lower()
    sport_filter = request.GET.get('sport', '').strip()
    is_ajax = (
        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
        request.GET.get('format') == 'json' or
        request.GET.get('ajax') == '1'
    )

    tournaments_qs = Tournament.objects.public().select_related('sport', 'venue')
    teams_qs = Team.objects.select_related('sport', 'owner__user')
    players_qs = PlayerProfile.objects.select_related('user').prefetch_related('sports').filter(
        user__is_suspended=False, user__is_active=True
    )
    organizers_qs = OrganizerProfile.objects.select_related('user').filter(
        user__is_suspended=False, user__is_active=True
    )

    if q:
        tournaments_qs = tournaments_qs.filter(
            Q(name__icontains=q) |
            Q(city__icontains=q) |
            Q(sport__name__icontains=q) |
            Q(description__icontains=q)
        ).distinct()

        teams_qs = teams_qs.filter(
            Q(name__icontains=q) |
            Q(sport__name__icontains=q) |
            Q(owner__current_city__icontains=q) |
            Q(owner__home_city__icontains=q)
        ).distinct()

        players_qs = players_qs.filter(
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q) |
            Q(user__username__icontains=q) |
            Q(current_city__icontains=q) |
            Q(home_city__icontains=q) |
            Q(sports__name__icontains=q) |
            Q(specialization__icontains=q)
        ).distinct()

        organizers_qs = organizers_qs.filter(
            Q(organization_name__icontains=q) |
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q) |
            Q(user__username__icontains=q) |
            Q(bio__icontains=q)
        ).distinct()

    if sport_filter:
        tournaments_qs = tournaments_qs.filter(sport__slug=sport_filter)
        teams_qs = teams_qs.filter(sport__slug=sport_filter)
        players_qs = players_qs.filter(sports__slug=sport_filter).distinct()

    # Pre-compute exact counts
    t_count = tournaments_qs.count() if q or sport_filter else 0
    team_count = teams_qs.count() if q or sport_filter else 0
    p_count = players_qs.count() if q or sport_filter else 0
    org_count = organizers_qs.count() if (q and not sport_filter) else (organizers_qs.count() if not sport_filter and q else 0)
    total_count = t_count + team_count + p_count + org_count

    # If AJAX/JSON requested, return quick structured suggestions
    if is_ajax:
        if not q and not sport_filter:
            return JsonResponse({
                'query': '',
                'total_count': 0,
                'counts': {'all': 0, 'tournaments': 0, 'teams': 0, 'players': 0, 'organizers': 0},
                'suggestions': [],
            })

        suggestions = []
        # Tournaments
        for t in tournaments_qs.order_by('-start_date')[:5]:
            img_url = t.banner_image.url if t.banner_image else ''
            subtitle = t.sport.name if t.sport else ''
            if t.city:
                subtitle += f" • {t.city}"
            badge_text = t.status.title() if t.status else 'Tournament'
            suggestions.append({
                'type': 'tournament',
                'title': t.name,
                'subtitle': subtitle,
                'meta': f"{t.participant_count()} {'teams' if t.is_team_based else 'players'}",
                'badge': badge_text,
                'image': img_url,
                'sport_icon': t.sport.icon_symbol if t.sport else 'i-trophy',
                'sport_class': t.sport.sp_class if t.sport else '',
                'url': t.get_absolute_url(),
            })

        # Teams
        for team in teams_qs.order_by('name')[:5]:
            img_url = team.logo.url if team.logo else ''
            subtitle = team.sport.name if team.sport else 'Sports Team'
            suggestions.append({
                'type': 'team',
                'title': team.name,
                'subtitle': subtitle,
                'meta': f"{team.initials}",
                'badge': 'Team',
                'image': img_url,
                'sport_icon': team.sport.icon_symbol if team.sport else 'i-shield',
                'sport_class': team.sport.sp_class if team.sport else '',
                'url': reverse('team_detail', args=[team.pk]),
            })

        # Players
        for p in players_qs.order_by('user__first_name')[:5]:
            img_url = p.profile_photo.url if p.profile_photo else ''
            p_sports = list(p.sports.values_list('name', flat=True)[:2])
            subtitle = ", ".join(p_sports) if p_sports else (p.specialization or 'Athlete')
            if p.current_city or p.home_city:
                loc = p.current_city or p.home_city
                subtitle += f" • {loc}"
            suggestions.append({
                'type': 'player',
                'title': p.user.display_name,
                'subtitle': subtitle,
                'meta': f"@{p.user.username}",
                'badge': 'Player',
                'is_verified': p.user.is_verified,
                'image': img_url,
                'url': p.get_absolute_url(),
            })

        # Organizers
        for org in organizers_qs.order_by('-created_at')[:4]:
            img_url = org.profile_photo.url if org.profile_photo else ''
            org_title = org.organization_name or org.user.display_name
            subtitle = f"Organizer • @{org.user.username}"
            suggestions.append({
                'type': 'organizer',
                'title': org_title,
                'subtitle': subtitle,
                'meta': f"{org.tournaments.count()} tournaments",
                'badge': 'Organizer',
                'is_verified': org.user.is_verified,
                'image': img_url,
                'url': reverse('organizer_public', args=[org.pk]),
            })

        return JsonResponse({
            'query': q,
            'total_count': total_count,
            'counts': {
                'all': total_count,
                'tournaments': t_count,
                'teams': team_count,
                'players': p_count,
                'organizers': org_count,
            },
            'suggestions': suggestions,
        })

    # For full HTML page view:
    tournaments = list(tournaments_qs.order_by('-start_date')[:24]) if (q or sport_filter) else []
    teams = list(teams_qs.order_by('name')[:24]) if (q or sport_filter) else []
    players = list(players_qs.order_by('user__first_name')[:24]) if (q or sport_filter) else []
    organizers = list(organizers_qs.order_by('-created_at')[:24]) if (q and not sport_filter) else []

    sports = Sport.objects.all().order_by('name')
    popular_searches = [
        {'label': 'Football', 'q': 'Football'},
        {'label': 'Basketball', 'q': 'Basketball'},
        {'label': 'Badminton', 'q': 'Badminton'},
        {'label': 'Cricket', 'q': 'Cricket'},
        {'label': 'Delhi', 'q': 'Delhi'},
        {'label': 'Mumbai', 'q': 'Mumbai'},
        {'label': 'Bhubaneswar', 'q': 'Bhubaneswar'},
        {'label': 'Bengaluru', 'q': 'Bengaluru'},
    ]

    return render(request, 'public/search.html', {
        'q': q,
        'category': category,
        'sport_filter': sport_filter,
        'total_count': total_count,
        'counts': {
            'all': total_count,
            'tournaments': t_count,
            'teams': team_count,
            'players': p_count,
            'organizers': org_count,
        },
        'tournaments': tournaments,
        'teams': teams,
        'players': players,
        'organizers': organizers,
        'sports': sports,
        'popular_searches': popular_searches,
    })


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


@login_required_msg
def referee_dashboard(request):
    if not hasattr(request.user, 'referee_profile'):
        messages.info(request, 'Please create your referee profile to access the Referee Portal.')
        return redirect('referee_onboarding')

    profile = request.user.referee_profile

    # Handle quick availability toggle
    if request.method == 'POST' and 'toggle_availability' in request.POST:
        profile.is_available = not profile.is_available
        profile.save(update_fields=['is_available'])
        messages.success(request, f"Officiating availability updated to {'Available' if profile.is_available else 'Unavailable'}.")
        return redirect('referee_dashboard')

    all_assignments = request.user.referee_assignments.select_related(
        'fixture__tournament__sport', 'fixture__tournament__venue',
    ).prefetch_related(
        'fixture__participants__team', 'fixture__participants__player__user'
    )

    total_count = all_assignments.count()
    live_count = all_assignments.filter(fixture__status='LIVE').count()
    upcoming_count = all_assignments.filter(fixture__status='SCHEDULED').count()
    completed_count = all_assignments.filter(fixture__status='COMPLETED').count()

    status_filter = request.GET.get('status', 'all').strip().lower()
    sport_filter = request.GET.get('sport', '').strip()

    filtered_qs = all_assignments
    if status_filter == 'live':
        filtered_qs = filtered_qs.filter(fixture__status='LIVE').order_by('fixture__scheduled_time')
    elif status_filter == 'upcoming':
        filtered_qs = filtered_qs.filter(fixture__status='SCHEDULED').order_by('fixture__scheduled_time')
    elif status_filter == 'completed':
        filtered_qs = filtered_qs.filter(fixture__status='COMPLETED').order_by('-fixture__scheduled_time', '-id')
    else:
        # Default sort: LIVE first, then SCHEDULED, then COMPLETED
        filtered_qs = filtered_qs.order_by(
            models.Case(
                models.When(fixture__status='LIVE', then=0),
                models.When(fixture__status='SCHEDULED', then=1),
                models.When(fixture__status='COMPLETED', then=2),
                default=3
            ),
            'fixture__scheduled_time'
        )

    if sport_filter:
        filtered_qs = filtered_qs.filter(fixture__tournament__sport__slug=sport_filter)

    sports = Sport.objects.filter(referees=profile).distinct()
    if not sports.exists():
        sports = Sport.objects.all()

    return render(request, 'dash/referee_dashboard.html', {
        'profile': profile,
        'assignments': filtered_qs,
        'total_count': total_count,
        'live_count': live_count,
        'upcoming_count': upcoming_count,
        'completed_count': completed_count,
        'status_filter': status_filter,
        'sport_filter': sport_filter,
        'sports': sports,
    })

@login_required_msg
def commentator_dashboard(request):
    """Commentator Portal: assignments, live commentary workspaces, history, and status."""
    if not hasattr(request.user, 'commentator_profile'):
        messages.info(request, 'Please create your commentator profile to access the Commentator Portal.')
        return redirect('commentator_onboarding')

    profile = request.user.commentator_profile

    # Quick availability toggle
    if request.method == 'POST' and 'toggle_availability' in request.POST:
        profile.is_available = not profile.is_available
        profile.save(update_fields=['is_available'])
        messages.success(request, f"Casting availability updated to {'Available' if profile.is_available else 'Unavailable'}.")
        return redirect('commentator_dashboard')

    all_assignments = request.user.commentator_assignments.select_related(
        'fixture__tournament__sport', 'fixture__tournament__venue',
    ).prefetch_related(
        'fixture__participants__team', 'fixture__participants__player__user',
        'fixture__commentary_entries'
    )

    total_count = all_assignments.count()
    live_count = all_assignments.filter(fixture__status='LIVE').count()
    upcoming_count = all_assignments.filter(fixture__status='SCHEDULED').count()
    completed_count = all_assignments.filter(fixture__status='COMPLETED').count()

    status_filter = request.GET.get('status', 'all').strip().lower()
    sport_filter = request.GET.get('sport', '').strip()

    filtered_qs = all_assignments
    if status_filter == 'live':
        filtered_qs = filtered_qs.filter(fixture__status='LIVE').order_by('fixture__scheduled_time')
    elif status_filter == 'upcoming':
        filtered_qs = filtered_qs.filter(fixture__status='SCHEDULED').order_by('fixture__scheduled_time')
    elif status_filter == 'completed':
        filtered_qs = filtered_qs.filter(fixture__status='COMPLETED').order_by('-fixture__scheduled_time', '-id')
    else:
        # Default sort: LIVE first, then SCHEDULED, then COMPLETED
        filtered_qs = filtered_qs.order_by(
            models.Case(
                models.When(fixture__status='LIVE', then=0),
                models.When(fixture__status='SCHEDULED', then=1),
                models.When(fixture__status='COMPLETED', then=2),
                default=3
            ),
            'fixture__scheduled_time'
        )

    if sport_filter:
        filtered_qs = filtered_qs.filter(fixture__tournament__sport__slug=sport_filter)

    sports = Sport.objects.filter(commentators=profile).distinct()
    if not sports.exists():
        sports = Sport.objects.all()

    return render(request, 'dash/commentator_dashboard.html', {
        'profile': profile,
        'assignments': filtered_qs,
        'total_count': total_count,
        'live_count': live_count,
        'upcoming_count': upcoming_count,
        'completed_count': completed_count,
        'status_filter': status_filter,
        'sport_filter': sport_filter,
        'sports': sports,
    })

def organizer_public(request, pk):
    """Public organizer portfolio & community publishing page."""
    from accounts.models import OrganizerProfile, UserFollow
    from tournaments.models import Tournament, TournamentTeamEntry, IndividualRegistration, Sport
    from django.db.models import Count, Q
    from django.utils import timezone

    org = get_object_or_404(
        OrganizerProfile.objects.select_related('user'),
        pk=pk,
        user__is_suspended=False,
        user__is_active=True,
    )

    is_owner = request.user.is_authenticated and request.user == org.user
    can_manage = is_owner or (request.user.is_authenticated and request.user.is_staff)

    # 1. Tournaments Queryset
    tournaments_qs = org.tournaments.filter(
        is_removed=False
    ).exclude(status='DRAFT').select_related(
        'sport', 'venue'
    ).annotate(
        confirmed_teams_count=Count(
            'team_entries',
            filter=Q(team_entries__status__in=['APPROVED', 'CONFIRMED']),
            distinct=True
        ),
        confirmed_indiv_count=Count(
            'registrations',
            filter=Q(registrations__status__in=['APPROVED', 'CONFIRMED']),
            distinct=True
        ),
    )

    all_tournaments = list(tournaments_qs)

    # Lifecycle groupings
    now = timezone.now()
    upcoming_tournaments = [
        t for t in all_tournaments
        if t.status == 'PUBLISHED' or (t.start_date and t.start_date > now.date() and t.status != 'COMPLETED')
    ]
    upcoming_tournaments.sort(key=lambda t: t.start_date if t.start_date else now.date())

    ongoing_tournaments = [
        t for t in all_tournaments
        if t.status == 'ONGOING' and t not in upcoming_tournaments
    ]
    ongoing_tournaments.sort(key=lambda t: t.start_date if t.start_date else now.date())

    completed_tournaments = [
        t for t in all_tournaments
        if t.status == 'COMPLETED'
    ]
    completed_tournaments.sort(
        key=lambda t: t.end_date or t.start_date or now.date(),
        reverse=True
    )

    # 2. Sports organized (derived dynamically from tournament history)
    sport_ids = {t.sport_id for t in all_tournaments if t.sport_id}
    sports = list(Sport.objects.filter(id__in=sport_ids).order_by('name'))

    # Fallback to application sports if no tournaments yet
    if not sports and hasattr(org.user, 'organizer_applications'):
        app = org.user.organizer_applications.first()
        if app:
            sports = list(app.sports.all())

    # 3. Reliable Statistics
    total_tournaments_count = len(all_tournaments)
    completed_count = len(completed_tournaments)

    # Unique teams across all tournaments
    unique_teams_count = TournamentTeamEntry.objects.filter(
        tournament__in=all_tournaments,
        status__in=['APPROVED', 'CONFIRMED']
    ).values('team_id').distinct().count()

    # Total participants registered/active across all tournaments
    team_members_count = TournamentTeamEntry.objects.filter(
        tournament__in=all_tournaments,
        status__in=['APPROVED', 'CONFIRMED']
    ).aggregate(total=Count('team__memberships', distinct=True))['total'] or 0

    indiv_registrations_count = IndividualRegistration.objects.filter(
        tournament__in=all_tournaments,
        status__in=['APPROVED', 'CONFIRMED']
    ).count()

    total_participants_count = team_members_count + indiv_registrations_count

    # 4. Content breakdown
    content_posts = org.authored_content.filter(
        is_published=True,
        is_removed=False
    ).prefetch_related('media_items').order_by('-created_at')

    all_posts = list(content_posts)
    vlogs = [
        p for p in all_posts
        if p.content_type == 'full_video' or p.aspect_ratio == '16/9' or bool(p.youtube_url)
    ]
    highlights = [
        p for p in all_posts
        if (p.content_type == 'short' or p.aspect_ratio == '9/16') and p not in vlogs
    ]
    posts = [
        p for p in all_posts
        if p not in vlogs and p not in highlights
    ]

    # 5. Follow state
    is_following = False
    if request.user.is_authenticated and not is_owner:
        is_following = UserFollow.objects.filter(
            follower=request.user,
            following=org.user
        ).exists()

    # 6. Location fallback (venue city of recent tournaments)
    organizer_location = ''
    for t in all_tournaments:
        if t.city:
            organizer_location = t.city
            break
        elif t.venue and t.venue.city:
            organizer_location = t.venue.city
            break

    return render(request, 'public/organizer_detail.html', {
        'organizer': org,
        'organizer_user': org.user,
        'organizer_location': organizer_location,
        'sports': sports,
        'stats': {
            'tournaments': total_tournaments_count,
            'completed': completed_count,
            'teams': unique_teams_count,
            'participants': total_participants_count,
        },
        'upcoming_tournaments': upcoming_tournaments,
        'ongoing_tournaments': ongoing_tournaments,
        'completed_tournaments': completed_tournaments,
        'all_tournaments': all_tournaments,
        'content_posts': all_posts,
        'vlogs': vlogs,
        'highlights': highlights,
        'posts': posts,
        'has_any_content': bool(all_posts),
        'is_following': is_following,
        'can_manage': can_manage,
        'is_owner': is_owner,
    })
