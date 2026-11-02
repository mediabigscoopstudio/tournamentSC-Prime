"""Read-only aggregation over existing fixture/result data (PLAYER-03/04)."""
from django.db.models import Q

from .models import Fixture, Tournament


def compute_basketball_team_stats(fixture, ordered_parts=None):
    """Compute basketball team stats (Points, Field Goals, Free Throws, Rebounds, Assists, Steals, Fouls)
    and their comparison percentages for the two competing teams.
    """
    if ordered_parts is None:
        ordered_parts = list(fixture.ordered_participants())
    if len(ordered_parts) != 2:
        return None

    team_a = ordered_parts[0]
    team_b = ordered_parts[1]

    stats = {
        'team_a': {
            'id': team_a.id,
            'name': team_a.name,
            'points': int(team_a.score or 0),
            'field_goals': 0,
            'free_throws_made': 0,
            'free_throws_att': 0,
            'rebounds': 0,
            'assists': 0,
            'steals': 0,
            'fouls': int(team_a.fouls or 0),
        },
        'team_b': {
            'id': team_b.id,
            'name': team_b.name,
            'points': int(team_b.score or 0),
            'field_goals': 0,
            'free_throws_made': 0,
            'free_throws_att': 0,
            'rebounds': 0,
            'assists': 0,
            'steals': 0,
            'fouls': int(team_b.fouls or 0),
        }
    }

    events = list(fixture.events.all())
    foul_counts = {'team_a': 0, 'team_b': 0}

    for ev in events:
        target = 'team_a' if ev.participant_id == team_a.id else ('team_b' if ev.participant_id == team_b.id else None)
        if not target:
            continue

        if ev.event_type == 'foul':
            foul_counts[target] += 1
        elif ev.event_type == 'rebound':
            stats[target]['rebounds'] += 1
        elif ev.event_type == 'assist':
            stats[target]['assists'] += 1
        elif ev.event_type == 'steal':
            stats[target]['steals'] += 1
        elif ev.event_type == 'score':
            snap = ev.score_snapshot or {}
            raw_pts = snap.get('points')
            pts = None
            if raw_pts is not None:
                try:
                    pts = int(float(raw_pts))
                except (ValueError, TypeError):
                    pts = None
            if pts is None:
                desc = ev.description or ''
                if '+1' in desc or 'FT' in desc or 'Free Throw' in desc:
                    pts = 1
                elif '+3' in desc:
                    pts = 3
                elif '+2' in desc:
                    pts = 2
                else:
                    pts = 2

            is_ft = bool(snap.get('is_ft') or snap.get('stat') == 'free_throw' or pts == 1)
            if is_ft:
                stats[target]['free_throws_made'] += 1
                stats[target]['free_throws_att'] += 1
            elif pts in (2, 3):
                stats[target]['field_goals'] += 1

    stats['team_a']['fouls'] = max(stats['team_a']['fouls'], foul_counts['team_a'])
    stats['team_b']['fouls'] = max(stats['team_b']['fouls'], foul_counts['team_b'])

    def _calc_pcts(a_val, b_val):
        tot = (a_val or 0) + (b_val or 0)
        if tot <= 0:
            return 0, 0
        p_a = round(((a_val or 0) / tot) * 100)
        p_b = 100 - p_a
        return p_a, p_b

    for key in ['points', 'field_goals', 'rebounds', 'assists', 'steals', 'fouls']:
        p_a, p_b = _calc_pcts(stats['team_a'][key], stats['team_b'][key])
        stats['team_a'][f'{key}_pct'] = p_a
        stats['team_b'][f'{key}_pct'] = p_b

    p_a, p_b = _calc_pcts(stats['team_a']['free_throws_made'], stats['team_b']['free_throws_made'])
    stats['team_a']['free_throws_pct'] = p_a
    stats['team_b']['free_throws_pct'] = p_b

    return stats



def _player_tournaments(profile):
    """Every tournament a player is in — via team roster or individual entry."""
    team_ids = list(profile.team_memberships.values_list('team_id', flat=True))
    q = Q(registrations__player=profile)
    if team_ids:
        q |= Q(team_entries__team_id__in=team_ids)
    return Tournament.objects.filter(q).distinct()


def player_schedule(profile):
    """Upcoming fixtures across all the player's tournaments, soonest first."""
    tournaments = _player_tournaments(profile)
    team_ids = list(profile.team_memberships.values_list('team_id', flat=True))
    fixtures = Fixture.objects.filter(
        tournament__in=tournaments, is_removed=False,
        status__in=['SCHEDULED', 'LIVE', 'POSTPONED'],
    ).filter(Q(participants__player=profile) | Q(participants__team_id__in=team_ids)) \
     .select_related('tournament', 'tournament__sport').distinct() \
     .order_by('scheduled_time', 'round_no', 'sequence')
    return fixtures


def player_results(profile):
    team_ids = list(profile.team_memberships.values_list('team_id', flat=True))
    tournaments = _player_tournaments(profile)
    fixtures = Fixture.objects.filter(
        tournament__in=tournaments, is_removed=False, status='COMPLETED',
    ).filter(Q(participants__player=profile) | Q(participants__team_id__in=team_ids)) \
     .select_related('tournament', 'tournament__sport').distinct() \
     .order_by('-updated_at')
    return fixtures


def player_history(profile):
    """Aggregate: tournaments played + a per-tournament summary row."""
    tournaments = _player_tournaments(profile).select_related('sport')
    return {
        'count': tournaments.count(),
        'tournaments': list(tournaments[:50]),
    }
