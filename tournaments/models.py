from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import TimeStamped
from . import constants as C


# ======================================================================
# Master data
# ======================================================================
class Sport(models.Model):
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    icon = models.CharField(max_length=8, blank=True, help_text='Emoji glyph')
    color_token = models.CharField(max_length=40, blank=True, help_text='CSS token, e.g. sport-basketball')
    format_type = models.CharField(max_length=12, default='TEAM')  # TEAM / INDIVIDUAL / BOTH
    default_format = models.CharField(max_length=20, choices=C.FORMAT_CHOICES, default=C.FORMAT_KNOCKOUT)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('sport_detail', args=[self.slug])

    @property
    def allowed_formats(self):
        cfg = C.SPORTS.get(self.slug)
        return cfg[5] if cfg else [self.default_format]

    @property
    def is_team_based(self):
        return self.format_type in C.TEAM_BASED_FORMAT_TYPES

    @property
    def tag_class(self):
        """CSS pill class from the style guide (esports slug is abbreviated)."""
        return {'mobile-esports': 'tag-esports'}.get(self.slug, f'tag-{self.slug}')

    @property
    def sp_class(self):
        """Sport-identity CSS class, e.g. 'sp-basketball'."""
        return {'mobile-esports': 'sp-esports'}.get(self.slug, f'sp-{self.slug}')

    @property
    def icon_symbol(self):
        """SVG sprite symbol id, e.g. 'i-basketball'."""
        return {'mobile-esports': 'i-esports'}.get(self.slug, f'i-{self.slug}')


class Venue(models.Model):
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=120)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    def __str__(self):
        return f'{self.name}, {self.city}'


# ======================================================================
# Tournament
# ======================================================================
class TournamentQuerySet(models.QuerySet):
    def public(self):
        """Anything an anonymous audience may see: never drafts or removed."""
        return self.exclude(status='DRAFT').filter(is_removed=False,
                                                    organizer__user__is_suspended=False)

    def live(self):
        return self.public().filter(status='ONGOING')

    def upcoming(self):
        return self.public().filter(status='PUBLISHED')

    def featured(self):
        return self.public().filter(is_featured=True).order_by('featured_order', '-created_at')


class Tournament(TimeStamped):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    sport = models.ForeignKey(Sport, on_delete=models.PROTECT, related_name='tournaments')
    organizer = models.ForeignKey('accounts.OrganizerProfile', on_delete=models.CASCADE,
                                  related_name='tournaments')
    description = models.TextField(blank=True)
    format = models.CharField(max_length=20, choices=C.FORMAT_CHOICES)
    venue = models.ForeignKey(Venue, on_delete=models.SET_NULL, null=True, blank=True)
    city = models.CharField(max_length=120, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    registration_deadline = models.DateTimeField(null=True, blank=True)
    banner_image = models.ImageField(upload_to='tournaments/', null=True, blank=True)
    entry_fee = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    prize_pool = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                     help_text='Total prize money, shown on the tournament card.')
    rules = models.TextField(blank=True, help_text='Format, tie-breaks, time control — shown on '
                                                   'the tournament page.')
    max_participants = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=C.TOURNAMENT_STATUS, default='DRAFT')
    is_featured = models.BooleanField(default=False)
    featured_order = models.PositiveIntegerField(default=0)
    youtube_url = models.URLField(blank=True, help_text='Default stream for the whole tournament')
    registration_form_url = models.URLField(
        blank=True, help_text='Google Form link for team/player registration — shown as a '
                              'button on the tournament page.')
    points_config = models.JSONField(default=C.default_points_config, blank=True)
    # How this tournament's fixtures are produced. Every existing tournament
    # defaults to CUSTOM — i.e. exactly the organiser-arranged flow it already
    # had — so this field changes nothing until an organizer opts in.
    fixture_mode = models.CharField(max_length=12, choices=C.FIXTURE_MODE_CHOICES,
                                    default=C.FIXTURE_MODE_CUSTOM)
    # Pool Stage + Knockout setup: {'num_pools', 'teams_per_pool',
    # 'qualifiers_per_pool'}. Empty for every tournament not using that mode.
    pool_config = models.JSONField(default=C.default_pool_config, blank=True)
    # Swiss-format setup: {'num_rounds': N}. Empty for every tournament not
    # using FORMAT_SWISS.
    swiss_config = models.JSONField(default=C.default_swiss_config, blank=True)
    fixtures_generated = models.BooleanField(default=False)
    is_removed = models.BooleanField(default=False)  # admin moderation (soft)

    objects = TournamentQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'is_featured']),
            models.Index(fields=['sport', 'status']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:200] or 'tournament'
            candidate, i = base, 1
            while Tournament.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                i += 1
                candidate = f'{base}-{i}'
            self.slug = candidate
        super().save(*args, **kwargs)

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})

    def get_absolute_url(self):
        return reverse('tournament_detail', args=[self.slug])

    # --- format helpers ------------------------------------------------
    @property
    def is_team_based(self):
        # Team formats use teams; individual formats use registrations. Esports
        # is a TEAM sport that runs a points table, so key off the sport.
        return self.sport.is_team_based

    @property
    def engine(self):
        from .engines import get_engine
        return get_engine(self)

    @property
    def is_bracket(self):
        return self.format == C.FORMAT_KNOCKOUT

    @property
    def is_points_table(self):
        return self.format == C.FORMAT_ROUND_ROBIN

    @property
    def uses_standings(self):
        return self.format in (C.FORMAT_ROUND_ROBIN, C.FORMAT_SWISS) or self.is_pool_stage

    # --- Pool Stage + Knockout ------------------------------------------
    @property
    def supports_pool_stage(self):
        """Whether the organizer may even be offered the pool option. Gated on
        the sport, so no other sport's fixtures page changes at all."""
        return self.sport.slug in C.POOL_STAGE_SPORTS

    @property
    def is_pool_stage(self):
        """True only once the organizer has actively switched this tournament
        to Pool Stage + Knockout. Everything else stays on Custom Fixtures."""
        return self.supports_pool_stage and self.fixture_mode == C.FIXTURE_MODE_POOL

    # --- Swiss ------------------------------------------------------------
    @property
    def is_swiss(self):
        return self.format == C.FORMAT_SWISS

    @property
    def swiss_num_rounds(self):
        """Organizer-configured round count, 0 when unset."""
        cfg = self.swiss_config or {}
        try:
            return max(0, int(cfg.get('num_rounds') or 0))
        except (TypeError, ValueError):
            return 0

    @property
    def swiss_round(self):
        """Highest Swiss round generated so far, 0 before Round 1 exists.

        Tracked explicitly in swiss_config (set by SwissEngine whenever it
        generates a round) rather than derived from Fixture.round_no, since
        the organizer can also add ad-hoc fixtures via "Add a fixture"
        (round numbers of their own choosing) alongside the auto-paired
        Swiss rounds — round_no alone isn't a reliable signal here.
        """
        cfg = self.swiss_config or {}
        try:
            return max(0, int(cfg.get('current_round') or 0))
        except (TypeError, ValueError):
            return 0

    @property
    def pool_settings(self):
        """(num_pools, teams_per_pool, qualifiers_per_pool) as ints, 0 when unset."""
        cfg = self.pool_config or {}

        def as_int(key):
            try:
                return max(0, int(cfg.get(key) or 0))
            except (TypeError, ValueError):
                return 0
        return as_int('num_pools'), as_int('teams_per_pool'), as_int('qualifiers_per_pool')

    @property
    def pool_assignment_mode(self):
        """'auto' (seed-order slice, default) or 'manual' (organizer-picked)."""
        cfg = self.pool_config or {}
        return cfg.get('assignment_mode') or 'auto'

    @property
    def pool_assignments(self):
        """{entrant_key: pool_label} from a manual pool setup, {} otherwise.

        `entrant_key` is the same 'team:<id>'/'reg:<id>' scheme used
        throughout the pool system (see _entrants_for/_manual_entrant_choices
        in engines.py/views.py) — not a raw integer, since Team and
        IndividualRegistration are separate PK sequences that could
        otherwise collide.
        """
        cfg = self.pool_config or {}
        raw = cfg.get('assignments') or {}
        return {str(k): v for k, v in raw.items() if str(k).strip()}

    @property
    def pool_extra_labels(self):
        """Organizer-created pool names that don't have any fixtures yet."""
        cfg = self.pool_config or {}
        raw = cfg.get('extra_labels') or []
        return [str(x).strip() for x in raw if str(x).strip()]

    def approved_entries(self):
        if self.is_team_based:
            return self.team_entries.filter(status='APPROVED').select_related('team')
        return self.registrations.filter(status='APPROVED').select_related('player__user')

    def participant_count(self):
        return self.approved_entries().count()


# ======================================================================
# Categories, Teams, Registrations
# ======================================================================
class EventCategory(models.Model):
    """Sub-brackets within one tournament: 5K/10K, weight classes, rating bands."""
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=120)
    max_participants = models.PositiveIntegerField(null=True, blank=True)
    entry_fee = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    distance_km = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='Distance in km (5, 10, 21.1, 42.2…). Enables pace on the leaderboard.')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.tournament.name} · {self.name}'


class Team(TimeStamped):
    name = models.CharField(max_length=120)
    sport = models.ForeignKey(Sport, on_delete=models.CASCADE, related_name='teams')
    logo = models.ImageField(upload_to='teams/', null=True, blank=True)
    captain = models.ForeignKey('accounts.PlayerProfile', on_delete=models.SET_NULL,
                                null=True, blank=True, related_name='captained_teams')

    def __str__(self):
        return self.name

    @property
    def initials(self):
        parts = [p for p in self.name.split() if p]
        return (''.join(p[0] for p in parts[:2]) or self.name[:2]).upper()


class TeamMembership(models.Model):
    ROLE_CHOICES = [('CAPTAIN', 'Captain'), ('MEMBER', 'Member')]
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='memberships')
    player = models.ForeignKey('accounts.PlayerProfile', on_delete=models.CASCADE,
                               related_name='team_memberships', null=True, blank=True)
    display_name = models.CharField(max_length=120, blank=True,
                                    help_text='For roster entries without a platform account')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='MEMBER')
    jersey_number = models.CharField(max_length=8, blank=True)
    phone_number = models.CharField(max_length=20, blank=True, help_text='Optional contact number')
    is_approved = models.BooleanField(default=True, help_text='False = pending player join request')
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['team', 'player'], name='uniq_team_player',
                                    condition=Q(player__isnull=False)),
        ]

    def __str__(self):
        return self.player.user.display_name if self.player else (self.display_name or 'Member')

    @property
    def name(self):
        if self.player:
            return self.player.user.display_name
        return self.display_name or 'Member'


class TournamentTeamEntry(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='team_entries')
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='entries')
    status = models.CharField(max_length=10, choices=C.ENTRY_STATUS, default='APPROVED')
    seed = models.PositiveIntegerField(null=True, blank=True)
    group_name = models.CharField(max_length=40, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['tournament', 'team'], name='uniq_tournament_team')]
        ordering = ['seed', 'registered_at']

    def __str__(self):
        return f'{self.team} in {self.tournament}'


class IndividualRegistration(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='registrations')
    event_category = models.ForeignKey(EventCategory, on_delete=models.SET_NULL, null=True, blank=True)
    player = models.ForeignKey('accounts.PlayerProfile', on_delete=models.CASCADE,
                               related_name='registrations', null=True, blank=True)
    display_name = models.CharField(max_length=120, blank=True,
                                    help_text='For entrants added by the organizer without an account')
    bib_number = models.CharField(max_length=12, blank=True)
    seed = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=C.ENTRY_STATUS, default='APPROVED')
    group_name = models.CharField(max_length=40, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tournament', 'player'], name='uniq_tournament_player',
                                    condition=Q(player__isnull=False)),
        ]
        ordering = ['seed', 'registered_at']

    def __str__(self):
        return f'{self.name} in {self.tournament}'

    @property
    def name(self):
        if self.player:
            return self.player.user.display_name
        return self.display_name or 'Entrant'


# ======================================================================
# Fixtures, participants, scoring, standings  (the format-agnostic core)
# ======================================================================
class Fixture(TimeStamped):
    """One scheduled contest of any shape: a 1v1 game, a 16-squad lobby, or a
    500-runner race. Participation lives in FixtureParticipant, so there are no
    fixed team_a / team_b columns."""
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='fixtures')
    event_category = models.ForeignKey(EventCategory, on_delete=models.SET_NULL, null=True, blank=True)
    round_name = models.CharField(max_length=60, blank=True)
    round_no = models.PositiveIntegerField(default=1)
    # Pool Stage + Knockout only (see constants.STAGE_*). Blank on every
    # fixture produced by any other flow, which is what every pre-existing
    # fixture and every other sport stays as.
    stage = models.CharField(max_length=12, blank=True)
    pool_name = models.CharField(max_length=40, blank=True,
                                 help_text='Pool label (A, B, C…) for pool-stage fixtures')
    sequence = models.PositiveIntegerField(default=0)
    bracket_position = models.PositiveIntegerField(null=True, blank=True)
    session_no = models.PositiveIntegerField(default=1, help_text='Time-trial: qualifying=1, race=2, ...')
    scheduled_time = models.DateTimeField(null=True, blank=True)
    venue_detail = models.CharField(max_length=120, blank=True, help_text='Court / lane / table')
    status = models.CharField(max_length=12, choices=C.FIXTURE_STATUS, default='SCHEDULED')
    youtube_url = models.URLField(blank=True)
    summary = models.TextField(blank=True)
    result_published = models.BooleanField(default=True)
    # Bracket wiring: winner of this fixture advances into `advances_to` slot.
    advances_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='feeders')
    advances_slot = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    is_removed = models.BooleanField(default=False)
    # Match clock (currently used by the basketball live-scoring view): when the
    # fixture went LIVE, plus accumulated extra time. The running clock is
    # simply `now - live_started_at + extra_time_seconds` — one continuous
    # timer, no separate extra-time system.
    live_started_at = models.DateTimeField(null=True, blank=True)
    extra_time_seconds = models.PositiveIntegerField(default=0)
    # Paused-clock support (basketball only): while set, the running clock is
    # frozen at `clock_paused_at - live_started_at + extra_time_seconds`.
    # Resuming shifts `live_started_at` forward by the paused duration instead
    # of introducing a separate accumulator, so the clock formula above never
    # has to change.
    clock_paused_at = models.DateTimeField(null=True, blank=True)
    # Quarter/period tracker (basketball only): 1-4 = quarters, 5+ = OT/2OT/...
    current_period = models.PositiveSmallIntegerField(default=1)
    # Quarter length, organizer-adjustable at any time (basketball only). The
    # quarter clock counts DOWN: remaining = quarter_length_seconds +
    # extra_time_seconds - elapsed_since(live_started_at) — so "add extra
    # time" correctly extends the quarter instead of draining it faster.
    quarter_length_seconds = models.PositiveIntegerField(default=600)
    # Shot clock (basketball only). It is no longer independently
    # start/stop-able by the organizer — it mirrors the quarter clock's
    # running/paused state exactly (see pause_clock/resume_clock/
    # reset_quarter_clock in views.py) and auto-cycles back to the full
    # duration the instant it hits 0, for as long as the quarter clock keeps
    # running. `shot_clock_seconds_remaining` is the frozen "remaining in the
    # current cycle" baseline whenever it isn't running; `shot_clock_running_since`
    # is the anchor timestamp while it's counting down, same shift-the-anchor
    # pattern as the quarter clock. `shot_clock_duration_seconds` is the
    # organizer-selected cycle length (12s/24s) that both the baseline and
    # every auto-reset use.
    shot_clock_duration_seconds = models.PositiveSmallIntegerField(default=24)
    shot_clock_seconds_remaining = models.PositiveSmallIntegerField(default=24)
    shot_clock_running_since = models.DateTimeField(null=True, blank=True)
    # Chosen once on the basketball pre-match setup screen (see score_fixture's
    # 'start' action) alongside quarter_length_seconds/shot_clock_duration_seconds.
    # Governs whether +1/+2/+3 and fouls ask for a scoring/fouling player
    # (individual attribution) or apply directly to the team only.
    individual_scoring_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ['round_no', 'sequence', 'id']

    def __str__(self):
        return f'{self.tournament.name} · {self.round_name or "Fixture"} #{self.sequence}'

    def get_absolute_url(self):
        return reverse('match_detail', args=[self.tournament.slug, self.pk])

    @property
    def effective_youtube_url(self):
        return self.youtube_url or self.tournament.youtube_url

    @property
    def is_live(self):
        return self.status == 'LIVE'

    def ordered_participants(self):
        return self.participants.select_related('team', 'player__user').order_by('slot', 'rank', '-score')

    @property
    def win_probability(self):
        """Live win-share for a head-to-head fixture, as a 0-100 int for the
        leading competitor's bar. Derived from the current score — no model
        state, so it can never go stale.

        Returns None when it would be meaningless (not head-to-head, no score
        yet, or both on zero).
        """
        parts = list(self.participants.all())
        if len(parts) != 2:
            return None
        a, b = parts
        if a.score is None or b.score is None:
            return None
        total = float(a.score) + float(b.score)
        if total <= 0:
            return None
        share = float(a.score) / total
        # Pull towards 50/50 so an early 2-0 doesn't read as a 100% certainty.
        damped = 0.5 + (share - 0.5) * 0.82
        return int(round(max(0.03, min(0.97, damped)) * 100))

    @property
    def win_probability_other(self):
        """The trailing competitor's share. Templates cannot do `100 - x`."""
        wp = self.win_probability
        return None if wp is None else 100 - wp

    @property
    def paused_quarter_remaining_seconds(self):
        """Countdown seconds remaining, frozen at the moment the clock was
        paused, or None while running. Lets the template render a static
        value instead of the JS having to know 'now' during a pause."""
        if not (self.clock_paused_at and self.live_started_at):
            return None
        elapsed = (self.clock_paused_at - self.live_started_at).total_seconds()
        remaining = self.quarter_length_seconds + self.extra_time_seconds - elapsed
        return max(0, int(remaining))

    def shot_clock_remaining_at(self, at):
        """Shot clock seconds remaining at a given instant, auto-wrapping back
        to the full duration every (duration + 1) seconds of running time —
        one extra second per cycle so the display actually reaches 0 (not
        just `duration - 1`) before resetting, matching a real shot-clock
        buzzer-then-reset. Frozen at `shot_clock_seconds_remaining` while not
        running (paused, or match not live)."""
        duration = self.shot_clock_duration_seconds
        if not self.shot_clock_running_since:
            return self.shot_clock_seconds_remaining
        elapsed_in_cycle = (duration - self.shot_clock_seconds_remaining) + \
            (at - self.shot_clock_running_since).total_seconds()
        cycle_length = duration + 1
        return duration - int(elapsed_in_cycle) % cycle_length

    @property
    def shot_clock_display_seconds(self):
        """Shot clock seconds for the initial server-rendered paint — JS ticks
        it client-side from here exactly like the quarter clock."""
        return self.shot_clock_remaining_at(timezone.now())

    @property
    def period_display(self):
        """1-4 -> '1st'/'2nd'/'3rd'/'4th', 5+ -> 'OT'/'2OT'/'3OT'/... (basketball only)."""
        n = self.current_period or 1
        if n <= 4:
            return {1: '1st', 2: '2nd', 3: '3rd', 4: '4th'}[n]
        ot = n - 4
        return 'OT' if ot == 1 else f'{ot}OT'


class FixtureParticipant(models.Model):
    """One row per competitor. Exactly one of team/player is set (enforced by a
    DB check constraint)."""
    fixture = models.ForeignKey(Fixture, on_delete=models.CASCADE, related_name='participants')
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True)
    player = models.ForeignKey('accounts.PlayerProfile', on_delete=models.CASCADE, null=True, blank=True)
    label = models.CharField(max_length=120, blank=True, help_text='Fallback name for account-less entrants')
    slot = models.PositiveIntegerField(default=0, help_text='0/1 for head-to-head; 0..n otherwise')
    score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    rank = models.PositiveIntegerField(null=True, blank=True)
    is_winner = models.BooleanField(null=True, blank=True)
    result_state = models.CharField(max_length=3, choices=C.RESULT_STATE, default='OK')
    time_ms = models.PositiveIntegerField(null=True, blank=True, help_text='Finish time in ms for time formats')
    stats = models.JSONField(default=dict, blank=True)
    fouls = models.PositiveSmallIntegerField(default=0, help_text='Team foul count (basketball only)')

    class Meta:
        ordering = ['slot', 'id']
        constraints = [
            models.CheckConstraint(
                name='fixtureparticipant_exactly_one_competitor',
                check=(Q(team__isnull=False, player__isnull=True) |
                       Q(team__isnull=True, player__isnull=False) |
                       # allow a placeholder (both null) only as a bracket TBD slot
                       Q(team__isnull=True, player__isnull=True)),
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def name(self):
        if self.team:
            return self.team.name
        if self.player:
            return self.player.user.display_name
        return self.label or 'TBD'

    @property
    def initials(self):
        if self.team:
            return self.team.initials
        n = self.name
        parts = [p for p in n.split() if p]
        return (''.join(p[0] for p in parts[:2]) or n[:2]).upper()

    @property
    def time_display(self):
        """Finish time as mm:ss.mmm — templates cannot call format_ms() with an
        argument, so the formatting has to live on the object."""
        from .utils import format_ms
        return format_ms(self.time_ms) if self.time_ms else ''

    # `stats` is denormalised at fixture-generation time (see engines._make_participant)
    # so a leaderboard row never has to walk back to the registration row.
    @property
    def bib(self):
        return (self.stats or {}).get('bib') or ''

    @property
    def rating(self):
        return (self.stats or {}).get('rating') or ''

    @property
    def kills(self):
        return (self.stats or {}).get('kills')

    @property
    def pace_display(self):
        """min/km for a timed result, when the category carries a distance."""
        cat = self.fixture.event_category
        distance = getattr(cat, 'distance_km', None)
        if not self.time_ms or not distance or float(distance) <= 0:
            return ''
        seconds_per_km = (self.time_ms / 1000.0) / float(distance)
        minutes, seconds = divmod(int(round(seconds_per_km)), 60)
        return f'{minutes}:{seconds:02d}'

    @property
    def result_chip(self):
        """'w' / 'l' / 'd' for a head-to-head result — drives the .res chip."""
        if self.fixture.status != 'COMPLETED' or self.score is None:
            return ''
        others = [p for p in self.fixture.participants.all() if p.id != self.id]
        if len(others) != 1 or others[0].score is None:
            return ''
        mine, theirs = float(self.score), float(others[0].score)
        return 'w' if mine > theirs else ('l' if mine < theirs else 'd')


class ScoreEvent(TimeStamped):
    """Timestamped live-scoring feed / commentary (SCORE-06)."""
    fixture = models.ForeignKey(Fixture, on_delete=models.CASCADE, related_name='events')
    participant = models.ForeignKey(FixtureParticipant, on_delete=models.SET_NULL, null=True, blank=True)
    event_type = models.CharField(max_length=30, default='note')
    description = models.CharField(max_length=280)
    score_snapshot = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.description[:50]


class Standing(models.Model):
    """Derived leaderboard row, recomputed on fixture completion (SCORE-03)."""
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='standings')
    group_name = models.CharField(max_length=40, blank=True)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True)
    player = models.ForeignKey('accounts.PlayerProfile', on_delete=models.CASCADE, null=True, blank=True)
    label = models.CharField(max_length=120, blank=True)
    played = models.PositiveIntegerField(default=0)
    won = models.PositiveIntegerField(default=0)
    lost = models.PositiveIntegerField(default=0)
    drawn = models.PositiveIntegerField(default=0)
    points = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    position = models.PositiveIntegerField(default=0)
    extra_stats = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position', '-points']

    def __str__(self):
        return f'{self.name} — {self.points} pts'

    @property
    def name(self):
        if self.team:
            return self.team.name
        if self.player:
            return self.player.user.display_name
        return self.label or '—'


class Highlight(TimeStamped):
    """Post-match recap + optional image (MEDIA-03)."""
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='highlights')
    fixture = models.ForeignKey(Fixture, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='highlights')
    title = models.CharField(max_length=160)
    description = models.TextField(max_length=800, blank=True)
    image = models.ImageField(upload_to='highlights/', null=True, blank=True)
    youtube_url = models.URLField(blank=True)
    best_performer = models.CharField(max_length=120, blank=True)
    duration_seconds = models.PositiveIntegerField(
        null=True, blank=True, help_text='Runtime of the clip, shown as a badge on the thumbnail.')
    view_count = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    is_removed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def is_published(self):
        return self.published_at is not None and not self.is_removed

    @property
    def duration_display(self):
        """Seconds -> m:ss for the thumbnail badge."""
        if not self.duration_seconds:
            return ''
        minutes, seconds = divmod(int(self.duration_seconds), 60)
        return f'{minutes}:{seconds:02d}'

    @property
    def views_display(self):
        """1234 -> '1.2k'. Keeps the meta line short on a card."""
        n = self.view_count or 0
        if n >= 1_000_000:
            return f'{n / 1_000_000:.1f}m'.replace('.0m', 'm')
        if n >= 1_000:
            return f'{n / 1_000:.1f}k'.replace('.0k', 'k')
        return str(n)
