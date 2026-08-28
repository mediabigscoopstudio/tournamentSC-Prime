"""Shared choices and the sport -> format-engine mapping.

The PRD (§5) is explicit that the *four format engines* are the real
architecture, not the seven sports. A new sport ships by mapping it to an
existing engine here — no new engine code.
"""

# --- Tournament formats (each maps to exactly one engine) ---------------
FORMAT_KNOCKOUT = 'KNOCKOUT'          # single-elimination bracket
FORMAT_ROUND_ROBIN = 'ROUND_ROBIN'    # points-table (multi-match)
FORMAT_TIME_TRIAL = 'TIME_TRIAL'      # time/position leaderboard, multi-session
FORMAT_SINGLE_EVENT = 'SINGLE_EVENT'  # one mass-start event, ranked once
FORMAT_SWISS = 'SWISS'                # Swiss pairing, one round generated at a time

FORMAT_CHOICES = [
    (FORMAT_KNOCKOUT, 'Single-elimination bracket'),
    (FORMAT_ROUND_ROBIN, 'Round-robin points table'),
    (FORMAT_TIME_TRIAL, 'Time-trial / leaderboard'),
    (FORMAT_SINGLE_EVENT, 'Single-event ranking'),
    (FORMAT_SWISS, 'Swiss pairing'),
]

# --- The 7 seeded sports, with their default + allowed formats ----------
# slug: (name, icon, color-token, format_type, default_format, [allowed_formats])
SPORTS = {
    'basketball': ('Basketball', '🏀', 'sport-basketball', 'TEAM', FORMAT_KNOCKOUT,
                   [FORMAT_KNOCKOUT, FORMAT_ROUND_ROBIN]),
    'badminton': ('Badminton', '🏸', 'sport-badminton', 'BOTH', FORMAT_KNOCKOUT,
                  [FORMAT_KNOCKOUT, FORMAT_ROUND_ROBIN]),
    'wrestling': ('Wrestling', '🤼', 'sport-wrestling', 'INDIVIDUAL', FORMAT_KNOCKOUT,
                  [FORMAT_KNOCKOUT]),
    'chess': ('Chess', '♟️', 'sport-chess', 'INDIVIDUAL', FORMAT_ROUND_ROBIN,
              [FORMAT_ROUND_ROBIN, FORMAT_KNOCKOUT, FORMAT_SWISS]),
    'mobile-esports': ('Mobile Esports', '🎮', 'sport-esports', 'TEAM', FORMAT_ROUND_ROBIN,
                       [FORMAT_ROUND_ROBIN]),
    'racing': ('Racing', '🏎️', 'sport-racing', 'BOTH', FORMAT_TIME_TRIAL,
               [FORMAT_TIME_TRIAL]),
    'pickleball': ('Pickleball', '🥒', 'sport-pickleball', 'BOTH', FORMAT_KNOCKOUT,
                   [FORMAT_KNOCKOUT, FORMAT_ROUND_ROBIN]),
    'marathon': ('Marathon', '🏃', 'sport-marathon', 'INDIVIDUAL', FORMAT_SINGLE_EVENT,
                 [FORMAT_SINGLE_EVENT]),
}

TEAM_BASED_FORMAT_TYPES = {'TEAM'}   # sports that always use teams
# Note: BOTH/INDIVIDUAL sports use individual registration by default.

# --- Fixture generation mode (additive to `format`, not a replacement) ---
# `format` still picks the engine for every existing tournament. `fixture_mode`
# only says *how* the organizer wants that tournament's fixtures produced:
#   CUSTOM — the existing organiser-arranged "Add fixture" flow (the default,
#            and the only mode any pre-existing tournament is ever in).
#   POOL   — the pool stage + auto knockout format (see POOL_STAGE_SPORTS
#            below). Works for both team-based and individual/registration-
#            based sports — the pool system keys entrants generically
#            ('team:<id>'/'reg:<id>'), not just teams.
FIXTURE_MODE_CUSTOM = 'CUSTOM'
FIXTURE_MODE_POOL = 'POOL'

FIXTURE_MODE_CHOICES = [
    (FIXTURE_MODE_CUSTOM, 'Custom Fixtures'),
    (FIXTURE_MODE_POOL, 'Pool Stage + Knockout'),
]

# Sports allowed to offer the Pool Stage + Knockout option. Every other sport
# keeps exactly the fixture options it has today — adding a sport here is the
# only change needed to widen it (the pool system itself is entrant-generic).
POOL_STAGE_SPORTS = {'basketball', 'badminton', 'pickleball'}

# Which half of a pool tournament a fixture belongs to. Blank on every fixture
# created by any other flow (custom, bracket generator, engine bulk-generate),
# so nothing outside the pool format ever has to look at it.
STAGE_POOL = 'POOL'
STAGE_KNOCKOUT = 'KNOCKOUT'

# Pool-stage match points. Kept as their own keys inside the existing
# per-tournament `points_config` blob so they are already configurable and
# cannot collide with the round-robin engine's win/draw/loss values.
POOL_POINTS_DEFAULTS = {'pool_win': 2, 'pool_loss': 1, 'pool_draw': 1}


def default_pool_config():
    """Per-tournament pool setup. Empty until the organizer configures it."""
    return {}


def default_swiss_config():
    """Per-tournament Swiss setup: {'num_rounds': N}. Empty until configured."""
    return {}

# --- Statuses -----------------------------------------------------------
TOURNAMENT_STATUS = [
    ('DRAFT', 'Draft'),
    ('PUBLISHED', 'Upcoming'),
    ('ONGOING', 'Live'),
    ('COMPLETED', 'Completed'),
    ('CANCELLED', 'Cancelled'),
]

FIXTURE_STATUS = [
    ('SCHEDULED', 'Scheduled'),
    ('LIVE', 'Live'),
    ('COMPLETED', 'Completed'),
    ('POSTPONED', 'Postponed'),
    ('CANCELLED', 'Cancelled'),
]

ENTRY_STATUS = [
    ('PENDING', 'Pending'),
    ('APPROVED', 'Approved'),
    ('REJECTED', 'Rejected'),
]

RESULT_STATE = [
    ('OK', 'Finished'),
    ('DNF', 'Did not finish'),
    ('DSQ', 'Disqualified'),
]


def default_points_config():
    """Per-tournament scoring knobs (FIX-03 / FIX-07)."""
    return {
        'win': 3, 'draw': 1, 'loss': 0,          # round-robin match points
        'kill_points': 1,                         # esports: points per kill
        'placement_points': {'1': 10, '2': 6, '3': 5, '4': 4, '5': 3, '6': 2},
    }
