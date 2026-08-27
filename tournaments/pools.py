"""Pool Stage + Knockout format (basketball only).

An *additional* fixture-generation system that sits alongside the existing
Custom Fixtures flow — it never replaces it. A tournament only ever reaches
this module when its organizer has explicitly switched `fixture_mode` to
`POOL` on a sport listed in `constants.POOL_STAGE_SPORTS`; every other
tournament keeps using the engine its `format` maps to, untouched.

The shape:

    teams -> pools (round-robin inside each pool) -> pool standings
          -> top N of each pool qualify -> auto-generated knockout bracket

Everything below is additive. Pool fixtures are marked `stage='POOL'` +
`pool_name='A'`, knockout fixtures `stage='KNOCKOUT'`; every fixture created
by any other flow keeps a blank `stage`, so no existing query, template or
engine is affected.
"""
import hashlib
import math
from itertools import groupby

from django.db import transaction

from . import constants as C
from .engines import (BracketEngine, _entrants_for, _make_participant, _num, _round_name,
                      _round_robin_rounds)
from .models import Fixture, Standing


class PoolConfigError(ValueError):
    """Raised when pool setup does not describe a runnable tournament. Carries
    the exact message the organizer should see."""


# ======================================================================
# Pool labels + configuration validation
# ======================================================================
def pool_label(index):
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'. Spreadsheet-style, so any pool count
    gets a distinct label without ever hardcoding a maximum."""
    label = ''
    n = index + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        label = chr(65 + rem) + label
    return label


def validate_pool_config(num_pools, teams_per_pool, qualifiers_per_pool, total_teams):
    """Return (is_valid, message).

    The message is what the organizer sees — for the headline check it is the
    arithmetic itself ("4 pools × 4 teams = 16 teams required"), so the fix is
    obvious without reading any prose.
    """
    if num_pools < 2:
        return False, 'Enter at least 2 pools.'
    if teams_per_pool < 2:
        return False, 'Each pool needs at least 2 teams so they can play each other.'
    required = num_pools * teams_per_pool
    if required != total_teams:
        return False, (f'{num_pools} pools × {teams_per_pool} teams = {required} teams required — '
                       f'this tournament has {total_teams} approved '
                       f'team{"" if total_teams == 1 else "s"}.')
    if qualifiers_per_pool < 1:
        return False, 'At least 1 team per pool must qualify for the knockout.'
    if qualifiers_per_pool > teams_per_pool:
        return False, (f'Only {teams_per_pool} teams are in each pool, so at most '
                       f'{teams_per_pool} can qualify.')
    if num_pools * qualifiers_per_pool < 2:
        return False, 'At least 2 teams in total must qualify for a knockout to be playable.'
    return True, ''


def validate_manual_pools(assignments, num_pools, qualifiers_per_pool, entrants):
    """Return (is_valid, message) for a manual team->pool assignment.

    `assignments` is {team_id: pool_label}. Every approved team must be
    assigned to one of the `num_pools` valid labels, and every pool must end
    up with enough teams to both play a round-robin (>= 2) and produce the
    requested number of qualifiers.
    """
    if num_pools < 2:
        return False, 'Enter at least 2 pools.'
    if qualifiers_per_pool < 1:
        return False, 'At least 1 team per pool must qualify for the knockout.'
    valid_labels = {pool_label(i) for i in range(num_pools)}
    counts = {label: 0 for label in valid_labels}
    unassigned = 0
    for e in entrants:
        team = e.get('team')
        label = assignments.get(team.id) if team else None
        if label not in valid_labels:
            unassigned += 1
            continue
        counts[label] += 1
    if unassigned:
        return False, (f'{unassigned} team{"" if unassigned == 1 else "s"} '
                       f'{"is" if unassigned == 1 else "are"} not assigned to a pool.')
    empty_or_small = [label for label in sorted(valid_labels, key=_label_sort_key)
                      if counts[label] < 2]
    if empty_or_small:
        return False, (f'Pool {", ".join(empty_or_small)} '
                       f'{"needs" if len(empty_or_small) == 1 else "need"} at least 2 teams '
                       f'so they can play each other.')
    too_few_qualifiers = [label for label in sorted(valid_labels, key=_label_sort_key)
                          if counts[label] < qualifiers_per_pool]
    if too_few_qualifiers:
        return False, (f'Pool {", ".join(too_few_qualifiers)} '
                       f'{"has" if len(too_few_qualifiers) == 1 else "have"} fewer teams than '
                       f'the {qualifiers_per_pool} qualifiers requested.')
    return True, ''


def config_summary(num_pools, teams_per_pool, qualifiers_per_pool):
    """One-line description of a valid setup, for the confirmation UI."""
    total = num_pools * teams_per_pool
    qualified = num_pools * qualifiers_per_pool
    per_pool_matches = teams_per_pool * (teams_per_pool - 1) // 2
    return (f'{num_pools} pools × {teams_per_pool} teams = {total} teams · '
            f'{per_pool_matches} matches per pool ({per_pool_matches * num_pools} total) · '
            f'top {qualifiers_per_pool} of each pool qualify ({qualified} teams) ')


# ======================================================================
# Ranking
# ======================================================================
def _draw_key(tournament_pk, pool, competitor_key):
    """Deterministic last-resort tie-break ("random draw").

    Random, but *stable*: derived from the tournament, pool and competitor, so
    a drawn-level pair keeps the same order every time standings are recomputed
    instead of shuffling on every page load.
    """
    seed = f'{tournament_pk}|{pool}|{competitor_key}'
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()


def rank_pool_rows(rows, head_to_head, tournament_pk, pool):
    """Order one pool's rows by the format's ranking rules:

        1. Tournament points
        2. Point difference
        3. Points scored
        4. Head-to-head (mini-table between the teams still level)
        5. Random draw

    Rules 1-3 are a plain sort. Head-to-head is only meaningful *between* the
    teams that are still tied, so it is applied per tied block rather than as a
    sort key — a head-to-head result against a team outside the block says
    nothing about who should be ahead inside it.
    """
    ordered = sorted(rows, key=lambda r: (-r['points'], -r['pd'], -r['pf']))
    out = []
    for _, block in groupby(ordered, key=lambda r: (r['points'], r['pd'], r['pf'])):
        block = list(block)
        if len(block) > 1:
            block = _break_tie(block, head_to_head, tournament_pk, pool)
        out.extend(block)
    return out


def _break_tie(block, head_to_head, tournament_pk, pool):
    """Order a set of still-level teams by their results against each other,
    then by the stable random draw."""
    keys = {r['key'] for r in block}
    mini = {r['key']: {'pts': 0.0, 'pd': 0, 'pf': 0} for r in block}
    for (a, b), (sa, sb, pa, pb) in head_to_head.items():
        if a not in keys or b not in keys:
            continue          # only results *within* the tied block count
        mini[a]['pts'] += pa
        mini[b]['pts'] += pb
        mini[a]['pd'] += sa - sb
        mini[b]['pd'] += sb - sa
        mini[a]['pf'] += sa
        mini[b]['pf'] += sb
    return sorted(block, key=lambda r: (
        -mini[r['key']]['pts'], -mini[r['key']]['pd'], -mini[r['key']]['pf'],
        _draw_key(tournament_pk, pool, r['key'])))


# ======================================================================
# Knockout seeding
# ======================================================================
def knockout_order(pools):
    """Order the qualifiers so consecutive pairs are the first-round matches.

    `pools` is a list (pool order) of lists (qualifiers, best first). Pools are
    cross-paired two at a time so a pool winner never opens against a team from
    its own pool:

        Pool A/B, top 2 -> A1 v B2, B1 v A2
        Pool C/D, top 2 -> C1 v D2, D1 v C2

    Generalised: for pool couple (X, Y) and rank r, X[r] meets Y[-1-r], with the
    better-placed side listed first. An odd pool count leaves one pool without a
    partner — its qualifiers go in straight rank order and the bracket builder's
    normal bye handling covers the remainder, so no team count is special-cased.
    """
    order = []
    for i in range(0, len(pools), 2):
        x = pools[i]
        y = pools[i + 1] if i + 1 < len(pools) else None
        if y is None:
            order.extend(x)
            continue
        depth = min(len(x), len(y))
        for r in range(depth):
            a, b = x[r], y[depth - 1 - r]
            # Better pool placing takes the home slot; ties keep pool order.
            if (b.get('pool_position') or 0) < (a.get('pool_position') or 0):
                a, b = b, a
            order.append(a)
            order.append(b)
        # Any surplus qualifiers (only possible if the pools ended up uneven).
        order.extend(x[depth:])
        order.extend(y[depth:])
    return order


# ======================================================================
# Engine
# ======================================================================
class PoolKnockoutEngine(BracketEngine):
    """Pool round-robins feeding an auto-generated single-elimination bracket.

    Subclasses `BracketEngine` purely to inherit its proven `_advance` wiring
    and its knockout `record_result` — the knockout half of a pool tournament
    behaves exactly like every other bracket on the platform, including byes
    and cascading auto-advancement.
    """
    format = C.FORMAT_KNOCKOUT

    # -- setup helpers --------------------------------------------------
    def _settings(self):
        return self.tournament.pool_settings

    def _points(self):
        cfg = self.tournament.points_config or {}
        return {k: float(cfg.get(k, default))
                for k, default in C.POOL_POINTS_DEFAULTS.items()}

    # -- pool stage -----------------------------------------------------
    @transaction.atomic
    def generate_fixtures(self, entrants=None):
        """Split the field into pools and play a full round-robin inside each.

        Replaces any existing fixtures for this tournament (same contract as
        every other generator on the platform) — the organizer confirms this
        before the button posts.
        """
        t = self.tournament
        num_pools, per_pool, qualifiers = self._settings()
        entrants = entrants if entrants is not None else _entrants_for(t)
        manual = t.pool_assignment_mode == 'manual'
        if manual:
            assignments = t.pool_assignments
            ok, message = validate_manual_pools(assignments, num_pools, qualifiers, entrants)
        else:
            ok, message = validate_pool_config(num_pools, per_pool, qualifiers, len(entrants))
        if not ok:
            raise PoolConfigError(message)

        self._clear_fixtures()
        author = getattr(t.organizer.user, 'id', None)
        seq = 0
        for p in range(num_pools):
            label = pool_label(p)
            if manual:
                members = [e for e in entrants if e.get('team') and assignments.get(e['team'].id) == label]
            else:
                members = entrants[p * per_pool:(p + 1) * per_pool]
            # Circle method: every pair meets once, nobody plays twice in the
            # same round — the same scheduling the basketball league already
            # uses, just scoped to one pool.
            for rno, pairs in enumerate(_round_robin_rounds(members), start=1):
                for a, b in pairs:
                    fx = Fixture.objects.create(
                        tournament=t, round_no=rno, sequence=seq,
                        round_name=f'Pool {label} · Round {rno}',
                        stage=C.STAGE_POOL, pool_name=label, created_by_id=author)
                    _make_participant(fx, a, 0)
                    _make_participant(fx, b, 1)
                    seq += 1
            self._remember_pool_membership(label, members)

        self.compute_standings()
        return t.fixtures.filter(is_removed=False).count()

    def _remember_pool_membership(self, label, members):
        """Denormalise the draw onto the team entries, so the pool a team is in
        is readable straight from the entry list (the admin console already
        surfaces `group_name`). Standings are still derived from fixtures, so
        this is a convenience, never a source of truth."""
        team_ids = [m['team'].id for m in members if m.get('team')]
        if team_ids:
            self.tournament.team_entries.filter(team_id__in=team_ids).update(group_name=label)

    # -- standings ------------------------------------------------------
    def _pool_fixtures(self):
        return self.tournament.fixtures.filter(
            stage=C.STAGE_POOL, is_removed=False).prefetch_related(
                'participants__team', 'participants__player__user')

    def _knockout_fixtures(self):
        return self.tournament.fixtures.filter(stage=C.STAGE_KNOCKOUT, is_removed=False)

    @staticmethod
    def _key_for(participant):
        if participant.team_id:
            return f'team:{participant.team_id}'
        if participant.player_id:
            return f'player:{participant.player_id}'
        return f'label:{participant.name}'

    @transaction.atomic
    def compute_standings(self):
        """One standings table per pool, keyed by `Standing.group_name`.

        Rows exist from the moment fixtures are generated (played=0), so every
        pool shows its full line-up before a ball is thrown.
        """
        t = self.tournament
        t.standings.all().delete()
        pts = self._points()
        _, _, qualifiers_per_pool = self._settings()

        pools = {}          # pool label -> {key: row}
        head_to_head = {}   # pool label -> {(key_a, key_b): (score_a, score_b, pts_a, pts_b)}

        def row_for(label, p):
            table = pools.setdefault(label, {})
            key = self._key_for(p)
            if key not in table:
                table[key] = {'key': key, 'team': p.team, 'player': p.player, 'label': p.name,
                              'played': 0, 'won': 0, 'lost': 0, 'drawn': 0,
                              'pf': 0, 'pa': 0, 'pd': 0, 'points': 0.0}
            return table[key]

        for fx in self._pool_fixtures():
            label = fx.pool_name
            parts = list(fx.participants.all())
            if len(parts) != 2:
                continue
            a, b = parts
            ra, rb = row_for(label, a), row_for(label, b)   # before the result check:
            if fx.status != 'COMPLETED':                    # an unplayed pool still lists
                continue                                    # every one of its teams
            sa, sb = _num(a.score), _num(b.score)
            if sa is None or sb is None:
                continue
            sa, sb = int(sa), int(sb)
            for row, scored, conceded in ((ra, sa, sb), (rb, sb, sa)):
                row['played'] += 1
                row['pf'] += scored
                row['pa'] += conceded
                row['pd'] = row['pf'] - row['pa']
            if sa > sb:
                ra['won'] += 1; rb['lost'] += 1
                pa, pb = pts['pool_win'], pts['pool_loss']
            elif sb > sa:
                rb['won'] += 1; ra['lost'] += 1
                pa, pb = pts['pool_loss'], pts['pool_win']
            else:
                # Basketball does not normally end level, but a finalized tie
                # must still score something rather than silently vanish.
                ra['drawn'] += 1; rb['drawn'] += 1
                pa = pb = pts['pool_draw']
            ra['points'] += pa
            rb['points'] += pb
            head_to_head.setdefault(label, {})[(ra['key'], rb['key'])] = (sa, sb, pa, pb)

        for label in sorted(pools, key=_label_sort_key):
            ranked = rank_pool_rows(list(pools[label].values()),
                                    head_to_head.get(label, {}), t.pk, label)
            for pos, r in enumerate(ranked, start=1):
                Standing.objects.create(
                    tournament=t, group_name=label, team=r['team'], player=r['player'],
                    label=r['label'], played=r['played'], won=r['won'], lost=r['lost'],
                    drawn=r['drawn'], points=round(r['points'], 1), position=pos,
                    extra_stats={'pf': r['pf'], 'pa': r['pa'], 'pd': r['pd'],
                                 'pool': label,
                                 'qualified': pos <= qualifiers_per_pool})

    # -- results --------------------------------------------------------
    @transaction.atomic
    def record_result(self, fixture, data):
        """Pool matches record a straight result; knockout matches keep the
        stock bracket behaviour (winner advances, draws cannot advance)."""
        if fixture.stage == C.STAGE_POOL:
            self._record_pool_result(fixture, data)
        else:
            super().record_result(fixture, data)
        self.compute_standings()
        if fixture.stage == C.STAGE_POOL:
            # The moment the last pool match is finalized the bracket appears —
            # no manual assignment of qualifiers.
            self.generate_knockout()

    def _record_pool_result(self, fixture, data):
        parts = list(fixture.participants.all())
        for p in parts:
            score = _num(data.get(str(p.id), {}).get('score'))
            if score is not None:
                p.score = score
            p.is_winner = False
            p.save(update_fields=['score', 'is_winner'])
        scores = [(_num(p.score), p) for p in parts]
        if all(s is not None for s, _ in scores) and scores:
            top = max(s for s, _ in scores)
            leaders = [p for s, p in scores if s == top]
            if len(leaders) == 1:
                leaders[0].is_winner = True
                leaders[0].save(update_fields=['is_winner'])
            else:
                for _, p in scores:      # a level pool match has no winner,
                    p.is_winner = None   # but it is still a finished match
                    p.save(update_fields=['is_winner'])
        if data.get('finalize'):
            # Unlike a bracket tie, a level pool match still completes — the
            # pool stage would otherwise never finish.
            fixture.status = 'COMPLETED'
            fixture.save(update_fields=['status'])

    # -- knockout -------------------------------------------------------
    def pool_stage_complete(self):
        pool_fixtures = self.tournament.fixtures.filter(stage=C.STAGE_POOL, is_removed=False)
        return (pool_fixtures.exists()
                and not pool_fixtures.exclude(status__in=('COMPLETED', 'CANCELLED')).exists())

    def knockout_generated(self):
        return self._knockout_fixtures().exists()

    def qualified_entrants(self):
        """The qualifying teams, ordered so consecutive pairs are round-1 matches."""
        _, _, qualifiers_per_pool = self._settings()
        if qualifiers_per_pool < 1:
            return []
        by_pool = {}
        for s in self.tournament.standings.filter(
                group_name__gt='').select_related('team', 'player__user').order_by('position'):
            by_pool.setdefault(s.group_name, []).append(s)
        pools = []
        for label in sorted(by_pool, key=_label_sort_key):
            pools.append([{
                'team': s.team, 'player': s.player, 'label': s.name,
                'pool': label, 'pool_position': s.position,
                'stats': {'pool': label, 'pool_position': s.position},
                'seed': 0,
            } for s in by_pool[label][:qualifiers_per_pool]])
        return knockout_order(pools)

    @transaction.atomic
    def generate_knockout(self, force=False):
        """Build the full bracket — quarterfinals through the final — from the
        pool qualifiers. Returns the number of knockout fixtures created.

        Silently does nothing until the pool stage is finished, or if a bracket
        already exists (so re-saving an earlier pool result can never wipe
        knockout scores); `force=True` is the organizer's explicit rebuild.
        """
        t = self.tournament
        if not self.pool_stage_complete():
            return 0
        if self.knockout_generated():
            if not force:
                return 0
            self._knockout_fixtures().delete()

        order = self.qualified_entrants()
        n = len(order)
        if n < 2:
            return 0

        # Same forward-built round sizing as BracketEngine: a bye only appears
        # in whichever round's own field is odd, so 12 or 20 qualifiers play a
        # full first round instead of padding out to the next power of two.
        sizes = [n]
        while sizes[-1] > 1:
            sizes.append(math.ceil(sizes[-1] / 2))
        num_rounds = len(sizes) - 1
        base = (t.fixtures.filter(stage=C.STAGE_POOL, is_removed=False)
                .order_by('-round_no').values_list('round_no', flat=True).first() or 0)
        author = getattr(t.organizer.user, 'id', None)

        grid = {}
        for r in range(1, num_rounds + 1):
            round_name = _round_name(sizes[r - 1])
            for i in range(sizes[r]):
                grid[(r, i)] = Fixture.objects.create(
                    tournament=t, round_no=base + r, sequence=i, bracket_position=i,
                    round_name=round_name, stage=C.STAGE_KNOCKOUT, created_by_id=author)
        for r in range(1, num_rounds):
            for i in range(sizes[r]):
                grid[(r, i)].advances_to = grid[(r + 1, i // 2)]
                grid[(r, i)].advances_slot = i % 2
                grid[(r, i)].save(update_fields=['advances_to', 'advances_slot'])

        matches_r1 = n // 2
        for i in range(matches_r1):
            fx = grid[(1, i)]
            _make_participant(fx, order[2 * i], 0)
            _make_participant(fx, order[2 * i + 1], 1)
        if n % 2:
            fx = grid[(1, matches_r1)]
            bye = _make_participant(fx, order[-1], 0)
            bye.is_winner = True
            bye.save(update_fields=['is_winner'])
            fx.status = 'COMPLETED'
            fx.summary = 'Bye'
            fx.save(update_fields=['status', 'summary'])
            self._advance(fx, bye)
        return self._knockout_fixtures().count()


def _label_sort_key(label):
    """'A' < 'B' < ... < 'Z' < 'AA' — shorter labels first, then alphabetical."""
    return (len(label), label)


# ======================================================================
# View context (shared by the organizer and public pages)
# ======================================================================
def pool_view_context(tournament):
    """Everything the pool templates render, built once so the organizer's
    fixtures page and the public tournament page can never drift apart.

    Returns pools (standings + line-up per pool), pool fixtures grouped by
    pool, the knockout rounds, and the champion once decided.
    """
    _, _, qualifiers_per_pool = tournament.pool_settings
    fixtures = list(tournament.fixtures.filter(is_removed=False).prefetch_related(
        'participants__team', 'participants__player__user').order_by('round_no', 'sequence', 'id'))

    standings = list(tournament.standings.filter(group_name__gt='')
                     .select_related('team', 'player__user').order_by('position'))
    by_pool = {}
    for s in standings:
        by_pool.setdefault(s.group_name, []).append(s)

    fixtures_by_pool = {}
    knockout = []
    for fx in fixtures:
        if fx.stage == C.STAGE_POOL:
            fixtures_by_pool.setdefault(fx.pool_name, []).append(fx)
        elif fx.stage == C.STAGE_KNOCKOUT:
            knockout.append(fx)

    labels = sorted(set(by_pool) | set(fixtures_by_pool) | set(tournament.pool_extra_labels),
                    key=_label_sort_key)
    pools = [{
        'label': label,
        'standings': by_pool.get(label, []),
        'fixtures': fixtures_by_pool.get(label, []),
        'played': sum(1 for f in fixtures_by_pool.get(label, []) if f.status == 'COMPLETED'),
        'total': len(fixtures_by_pool.get(label, [])),
    } for label in labels]

    rounds = {}
    for fx in knockout:
        rounds.setdefault(fx.round_no, {'name': fx.round_name, 'fixtures': []})
        rounds[fx.round_no]['fixtures'].append(fx)
    knockout_rounds = [rounds[k] for k in sorted(rounds)]
    for rnd in knockout_rounds:
        # Heading for a whole round: "Quarterfinals", but "Final" and
        # "Round of 12" must not pick up a stray plural.
        rnd['title'] = (f"{rnd['name']}s" if rnd['name'] in ('Quarterfinal', 'Semifinal')
                        else rnd['name'])

    champion = None
    if knockout_rounds:
        final = knockout_rounds[-1]['fixtures']
        if len(final) == 1 and final[0].status == 'COMPLETED':
            champion = next((p for p in final[0].participants.all() if p.is_winner), None)

    pool_fixtures = [f for f in fixtures if f.stage == C.STAGE_POOL]
    return {
        'pools': pools,
        'knockout_rounds': knockout_rounds,
        'champion': champion,
        'qualifiers_per_pool': qualifiers_per_pool,
        'pool_stage_total': len(pool_fixtures),
        'pool_stage_played': sum(1 for f in pool_fixtures if f.status == 'COMPLETED'),
        'pool_stage_complete': bool(pool_fixtures) and all(
            f.status in ('COMPLETED', 'CANCELLED') for f in pool_fixtures),
        'has_knockout': bool(knockout_rounds),
        'has_pool_draws': any(s.drawn for s in standings),
    }
