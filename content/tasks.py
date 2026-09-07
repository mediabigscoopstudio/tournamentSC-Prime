"""Achievement awarding — same Celery retry shape as tournaments/tasks.py.

Triggered from tournaments/services.py whenever a fixture completes (per-
participant check) or a tournament completes (whole-roster check), rather
than a new signal — it just adds a `.delay()` call alongside the existing
result-notification side effects.
"""
import logging

from celery import shared_task
from django.db.models import Sum

logger = logging.getLogger(__name__)


def _stats_for(profile):
    """Aggregate wins/tournaments-played for one PlayerProfile, from the
    existing Standing rows (recomputed by the scoring engine on every result)."""
    from tournaments.models import Standing
    agg = Standing.objects.filter(player=profile).aggregate(total_wins=Sum('won'))
    tournaments_played = Standing.objects.filter(player=profile).values('tournament_id').distinct().count()
    return {'wins': agg['total_wins'] or 0, 'tournaments': tournaments_played}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def check_and_award_achievements_task(self, user_id):
    from accounts.models import PlayerProfile

    from .models import Achievement, UserAchievement

    profile = PlayerProfile.objects.filter(user_id=user_id).first()
    if not profile:
        return
    try:
        stats = _stats_for(profile)
        already_earned = set(UserAchievement.objects.filter(
            user_id=user_id).values_list('achievement_id', flat=True))
        for achievement in Achievement.objects.filter(is_active=True).exclude(pk__in=already_earned):
            criteria = achievement.criteria or {}
            if all(stats.get(key, 0) >= threshold for key, threshold in criteria.items()):
                UserAchievement.objects.get_or_create(user_id=user_id, achievement=achievement)
    except Exception as exc:
        logger.warning('Achievement check failed for user %s: %s', user_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error('Giving up on achievement check for user %s', user_id)
