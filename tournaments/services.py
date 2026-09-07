"""Cross-cutting actions that sit above the engines: applying a score update,
firing notifications, and keeping tournament status in sync with its fixtures.
"""
from accounts.models import Follow, Notification

from .emails import send_tournament_completed


def apply_result(fixture, data, actor=None):
    """Record a result through the tournament's engine, then run side effects
    (status sync + notifications). Standings recalculation happens inside the
    engine (SCORE-03)."""
    tournament = fixture.tournament
    engine = tournament.engine
    was_completed = fixture.status == 'COMPLETED'
    engine.record_result(fixture, data)
    fixture.refresh_from_db()
    sync_tournament_status(tournament)
    if fixture.status == 'COMPLETED' and not was_completed:
        _notify_participants(fixture)
    return fixture


def sync_tournament_status(tournament):
    """Derive PUBLISHED / ONGOING / COMPLETED from fixture states. Never touches
    DRAFT or CANCELLED tournaments."""
    if tournament.status in ('DRAFT', 'CANCELLED'):
        return
    fixtures = tournament.fixtures.filter(is_removed=False)
    if not fixtures.exists():
        return
    statuses = set(fixtures.values_list('status', flat=True))
    new_status = tournament.status
    if statuses <= {'COMPLETED', 'CANCELLED'}:
        new_status = 'COMPLETED'
    elif 'LIVE' in statuses:
        new_status = 'ONGOING'
    elif statuses & {'SCHEDULED', 'POSTPONED'}:
        # some done, some pending -> ongoing once at least one is complete
        new_status = 'ONGOING' if 'COMPLETED' in statuses else 'PUBLISHED'
    if new_status != tournament.status:
        tournament.status = new_status
        tournament.save(update_fields=['status'])
        if new_status == 'COMPLETED':
            send_tournament_completed(tournament)


def _notify_participants(fixture):
    """Tell everyone with a stake in this fixture that the result is in:
    the competitors themselves, and anyone following the tournament."""
    seen = set()
    message = f'Result posted: {fixture.tournament.name} — {fixture.round_name}'
    url = fixture.get_absolute_url()

    def push(user):
        if user and user.id not in seen:
            seen.add(user.id)
            Notification.push(user, message, url=url, verb='result')

    participant_user_ids = set()
    for p in fixture.participants.select_related('team', 'player__user'):
        if p.player and p.player.user_id:
            push(p.player.user)
            participant_user_ids.add(p.player.user_id)
        elif p.team_id:
            for m in p.team.memberships.select_related('player__user'):
                if m.player and m.player.user_id:
                    push(m.player.user)
                    participant_user_ids.add(m.player.user_id)

    from content.tasks import check_and_award_achievements_task
    for user_id in participant_user_ids:
        check_and_award_achievements_task.delay(user_id)

    # Followers are the audience — neither playing nor organizing, but they asked.
    for follow in Follow.objects.filter(
            tournament=fixture.tournament).select_related('user'):
        push(follow.user)
