import math
import random
from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from .models import Fixture, FixtureParticipant, Standing


def _num(x):
    if x is None or x == '': return None
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def get_chess_standings_data(tournament, fixtures, entrants):
    """
    Computes points, direct encounter, buchholz, sonneborn-berger, wins, rating
    for Chess Swiss.
    Returns sorted list of dictionaries with all stats.
    """
    table = {}

    for e in entrants:
        k = e['key']
        table[k] = {
            'team': e['team'],
            'player': e['player'],
            'label': e['label'],
            'key': k,
            'played': 0,
            'wins': 0,
            'draws': 0,
            'losses': 0,
            'points': 0.0,
            'rating': (e['stats'] or {}).get('rating'),
            'opponents': [],  # list of opponent keys
            'opponent_results': {},  # opp_key -> score against them (for direct encounter / SB)
            'byes': 0,
        }

    # First pass: calculate points and basic stats
    for fx in fixtures:
        parts = list(fx.participants.all())
        if len(parts) == 1:
            # Bye
            k = _participant_key(parts[0], entrants)
            if k in table:
                table[k]['played'] += 1
                table[k]['points'] += 1.0
                table[k]['byes'] += 1
            continue
            
        if len(parts) == 2:
            a, b = parts
            sa, sb = _num(a.score), _num(b.score)
            if sa is None or sb is None:
                continue
                
            ka = _participant_key(a, entrants)
            kb = _participant_key(b, entrants)
            
            if ka in table and kb in table:
                table[ka]['played'] += 1
                table[kb]['played'] += 1
                table[ka]['opponents'].append(kb)
                table[kb]['opponents'].append(ka)
                table[ka]['opponent_results'][kb] = sa
                table[kb]['opponent_results'][ka] = sb
                
                table[ka]['points'] += sa
                table[kb]['points'] += sb
                
                if sa == 1:
                    table[ka]['wins'] += 1
                    table[kb]['losses'] += 1
                elif sa == 0.5:
                    table[ka]['draws'] += 1
                    table[kb]['draws'] += 1
                elif sa == 0:
                    table[ka]['losses'] += 1
                    table[kb]['wins'] += 1

    # Second pass: Buchholz & Sonneborn-Berger
    for k, r in table.items():
        buchholz = 0.0
        sb = 0.0
        for opp in r['opponents']:
            opp_pts = table[opp]['points']
            buchholz += opp_pts
            
            # SB: win -> full opp score, draw -> half opp score, loss -> 0
            score_vs_opp = r['opponent_results'].get(opp, 0)
            if score_vs_opp == 1:
                sb += opp_pts
            elif score_vs_opp == 0.5:
                sb += opp_pts * 0.5
                
        r['buchholz'] = round(buchholz, 2)
        r['sonneborn_berger'] = round(sb, 2)

    # Sort logic with Direct Encounter handling
    # For DE, we group by points, then look at sub-groups to see if they all played each other.
    # To do DE properly for any n-way tie, the standard is:
    # If all tied players played each other, sum their points against each other.
    
    def get_de_score(group, p_key):
        # sum of points against everyone else in this specific tie group
        # only valid if EVERYONE in the group played EVERYONE else in the group
        for other in group:
            if other != p_key:
                if other not in table[p_key]['opponent_results']:
                    return 0.0 # not a closed group, DE is invalid for this tiebreak
        
        return sum(table[p_key]['opponent_results'].get(other, 0.0) for other in group if other != p_key)

    # Group players by initial sort keys without DE
    rows = list(table.values())
    rows.sort(key=lambda r: (-r['points'], -r['buchholz'], -r['sonneborn_berger'], -r['wins'], -(r['rating'] or 0), r['label'].lower()))
    
    # We can inject DE score by finding point-ties, checking if they are a closed group, and if so sorting by DE.
    # To keep it robust, we'll assign a 'de_score' which is 0 by default, and computed for point-tied groups.
    point_groups = defaultdict(list)
    for r in rows:
        point_groups[r['points']].append(r['key'])
        
    for pts, group in point_groups.items():
        if len(group) > 1:
            for k in group:
                table[k]['direct_encounter'] = get_de_score(group, k)
        else:
            table[group[0]]['direct_encounter'] = 0.0

    # Final sort
    # Points (desc), Direct Encounter (desc), Buchholz (desc), SB (desc), Wins (desc), Rating (desc)
    rows.sort(key=lambda r: (
        -r['points'],
        -r.get('direct_encounter', 0.0),
        -r['buchholz'],
        -r['sonneborn_berger'],
        -r['wins'],
        -(r['rating'] or 0),
        r['label'].lower()
    ))
    
    return rows


def compute_chess_standings(tournament, fixtures, entrants):
    rows = get_chess_standings_data(tournament, fixtures, entrants)
    tournament.standings.all().delete()
    
    for pos, r in enumerate(rows, start=1):
        Standing.objects.create(
            tournament=tournament,
            team=r['team'],
            player=r['player'],
            label=r['label'],
            played=r['played'],
            won=r['wins'],
            lost=r['losses'],
            drawn=r['draws'],
            points=round(r['points'], 1),
            position=pos,
            extra_stats={
                'buchholz': r['buchholz'],
                'sonneborn_berger': r['sonneborn_berger'],
                'direct_encounter': r.get('direct_encounter', 0.0),
                'byes': r['byes'],
                'rating': r['rating']
            }
        )
    return rows


def _participant_key(p, entrants):
    if p.player_id:
        return f'reg:{next((e for e in entrants if e["player"] and e["player"].id == p.player_id), {"key": f"fp:{p.id}"})["key"].split(":")[-1]}'
    if p.name:
        return f'reg:{next((e for e in entrants if e["label"] == p.name), {"key": f"fp:{p.id}"})["key"].split(":")[-1]}'
    return f'fp:{p.id}'

# To ensure the key matches exact entrant keys:
def _get_k(p, entrants_by_id):
    if p.player_id and p.player_id in entrants_by_id['player']:
        return entrants_by_id['player'][p.player_id]
    if p.name and p.name in entrants_by_id['name']:
        return entrants_by_id['name'][p.name]
    return f'fp:{p.id}'


def generate_chess_swiss_round_1(tournament, entrants):
    n = len(entrants)
    if n < 2: return 0
    
    # Sort by rating desc, then seed
    sorted_entrants = sorted(entrants, key=lambda e: (-(e['stats'].get('rating') or 0), e['seed'], e['label'].lower()))
    
    bye = None
    if n % 2 != 0:
        bye = sorted_entrants.pop()
        n -= 1
        
    half = n // 2
    top = sorted_entrants[:half]
    bottom = sorted_entrants[half:]
    
    pairs = []
    # Assign colors: alternate White/Black
    for i in range(half):
        if i % 2 == 0:
            pairs.append((top[i], bottom[i]))  # top is White
        else:
            pairs.append((bottom[i], top[i]))  # bottom is White

    author = getattr(tournament.organizer.user, 'id', None)
    seq = tournament.fixtures.filter(is_removed=False).count()
    
    with transaction.atomic():
        tournament.fixtures.all().delete()
        
        for w, b in pairs:
            fx = Fixture.objects.create(
                tournament=tournament, round_no=1, sequence=seq,
                round_name='Round 1', created_by_id=author)
            _make_chess_participant(fx, w, 0)
            _make_chess_participant(fx, b, 1)
            seq += 1
            
        if bye:
            fx = Fixture.objects.create(
                tournament=tournament, round_no=1, sequence=seq,
                round_name='Round 1', created_by_id=author,
                status='COMPLETED', summary='Bye')
            bp = _make_chess_participant(fx, bye, 0)
            bp.score = 1.0
            bp.is_winner = True
            bp.save()
            
    return tournament.fixtures.count()


def _make_chess_participant(fixture, entrant, slot):
    return FixtureParticipant.objects.create(
        fixture=fixture,
        team=entrant['team'],
        player=entrant['player'],
        label=entrant['label'],
        slot=slot
    )


def generate_chess_swiss_next_round(tournament, current_round, entrants):
    fixtures = tournament.fixtures.filter(is_removed=False).prefetch_related('participants')
    
    # Validate all fixtures in current round are complete
    current_fixtures = [f for f in fixtures if f.round_no == current_round]
    if any(f.status not in ('COMPLETED', 'CANCELLED') for f in current_fixtures):
        raise ValueError("Cannot generate next round while current round is incomplete")

    next_round = current_round + 1
    
    # 1. Gather stats (points, played, byes, color history)
    eb_player = {e['player'].id: e['key'] for e in entrants if e['player']}
    eb_name = {e['label']: e['key'] for e in entrants if not e['player']}
    eb = {'player': eb_player, 'name': eb_name}
    
    points = {e['key']: 0.0 for e in entrants}
    byes = {e['key']: 0 for e in entrants}
    played = defaultdict(set)
    # color_balance: +1 for White, -1 for Black
    color_balance = {e['key']: 0 for e in entrants}
    
    for fx in fixtures:
        if fx.status not in ('COMPLETED', 'CANCELLED'): continue
        parts = list(fx.participants.all())
        if len(parts) == 1:
            k = _get_k(parts[0], eb)
            if k in points:
                points[k] += 1.0
                byes[k] += 1
        elif len(parts) == 2:
            p0, p1 = parts
            k0 = _get_k(p0, eb)
            k1 = _get_k(p1, eb)
            played[k0].add(k1)
            played[k1].add(k0)
            
            # Slot 0 = White, Slot 1 = Black
            if p0.slot == 0:
                if k0 in color_balance: color_balance[k0] += 1
                if k1 in color_balance: color_balance[k1] -= 1
            else:
                if k0 in color_balance: color_balance[k0] -= 1
                if k1 in color_balance: color_balance[k1] += 1
                
            s0, s1 = _num(p0.score), _num(p1.score)
            if s0 is not None and s1 is not None:
                if k0 in points: points[k0] += s0
                if k1 in points: points[k1] += s1

    # 2. Sort players by points, then rating, then seed
    sorted_entrants = sorted(entrants, key=lambda e: (
        -points[e['key']], 
        -(e['stats'].get('rating') or 0), 
        e['seed'], 
        e['label'].lower()
    ))
    
    # 3. Determine bye (if odd)
    n = len(sorted_entrants)
    bye_player = None
    active_entrants = sorted_entrants.copy()
    
    if n % 2 != 0:
        # Find the lowest ranked player who hasn't had a bye
        for i in range(n-1, -1, -1):
            if byes[sorted_entrants[i]['key']] == 0:
                bye_player = sorted_entrants[i]
                active_entrants.pop(i)
                break
        if not bye_player:
            # Fallback if everyone has had a bye (rare)
            bye_player = active_entrants.pop()
            
    # 4. Pair remaining players using backtracking to avoid repeats
    def solve_pairings(unpaired):
        if not unpaired:
            return []
            
        p1 = unpaired[0]
        p1k = p1['key']
        
        # Candidate opponents: prefer same score group, then rating
        # They are already sorted by this!
        for i in range(1, len(unpaired)):
            p2 = unpaired[i]
            p2k = p2['key']
            
            if p2k not in played[p1k]:
                # Try this pairing
                remaining = unpaired[1:i] + unpaired[i+1:]
                sub_solution = solve_pairings(remaining)
                if sub_solution is not None:
                    return [(p1, p2)] + sub_solution
                    
        return None

    pairings = solve_pairings(active_entrants)
    
    # If strict pairing fails due to impossible constraints, fallback to greedy with repeats allowed
    if pairings is None:
        pairings = []
        pool = active_entrants.copy()
        while pool:
            p1 = pool.pop(0)
            best_idx = 0
            for i, p2 in enumerate(pool):
                if p2['key'] not in played[p1['key']]:
                    best_idx = i
                    break
            p2 = pool.pop(best_idx)
            pairings.append((p1, p2))

    # 5. Assign colors to pairings
    final_pairs = []
    for p1, p2 in pairings:
        k1, k2 = p1['key'], p2['key']
        cb1, cb2 = color_balance[k1], color_balance[k2]
        
        # The one with lower balance gets White
        if cb1 < cb2:
            final_pairs.append((p1, p2)) # p1 White
        elif cb2 < cb1:
            final_pairs.append((p2, p1)) # p2 White
        else:
            # Random or based on rating? Let's just use original order (which is higher ranked gets White)
            final_pairs.append((p1, p2))

    # 6. Save atomically
    author = getattr(tournament.organizer.user, 'id', None)
    seq = tournament.fixtures.filter(is_removed=False).count()
    
    with transaction.atomic():
        for w, b in final_pairs:
            fx = Fixture.objects.create(
                tournament=tournament, round_no=next_round, sequence=seq,
                round_name=f'Round {next_round}', created_by_id=author)
            _make_chess_participant(fx, w, 0)
            _make_chess_participant(fx, b, 1)
            seq += 1
            
        if bye_player:
            fx = Fixture.objects.create(
                tournament=tournament, round_no=next_round, sequence=seq,
                round_name=f'Round {next_round}', created_by_id=author,
                status='COMPLETED', summary='Bye')
            bp = _make_chess_participant(fx, bye_player, 0)
            bp.score = 1.0
            bp.is_winner = True
            bp.save()
            
    return tournament.fixtures.filter(round_no=next_round, is_removed=False).count()
