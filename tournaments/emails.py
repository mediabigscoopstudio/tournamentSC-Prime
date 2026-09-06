"""Public entry points for organizer/player lifecycle emails.

Each just enqueues a Celery task (see tasks.py) — actual sending happens off
the request thread, from team@tournamentsc.com. Enqueuing itself is wrapped so
a broker outage can't break the view/service that triggered it.
"""
import logging

from .tasks import (send_fixtures_created_task, send_player_welcome_task,
                    send_tournament_completed_task, send_tournament_published_task)

logger = logging.getLogger(__name__)


def send_tournament_published(tournament):
    try:
        send_tournament_published_task.delay(tournament.pk)
    except Exception:
        logger.exception('Could not enqueue published email for %s', tournament.slug)


def send_fixtures_created(tournament):
    try:
        send_fixtures_created_task.delay(tournament.pk)
    except Exception:
        logger.exception('Could not enqueue fixtures-created email for %s', tournament.slug)


def send_tournament_completed(tournament):
    try:
        send_tournament_completed_task.delay(tournament.pk)
    except Exception:
        logger.exception('Could not enqueue completed email for %s', tournament.slug)


def send_player_welcome(player_profile, tournament):
    try:
        send_player_welcome_task.delay(player_profile.pk, tournament.pk)
    except Exception:
        logger.exception('Could not enqueue welcome email for player %s / %s',
                         player_profile.pk, tournament.slug)
