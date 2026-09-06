"""Async organizer/player lifecycle emails, sent from team@tournamentsc.com.

Same shape as support/tasks.py: a Celery task per email, retried a few times
on failure then logged and dropped, so a slow/unreachable SMTP server never
blocks the view or service that triggered it. The one difference from the
support mailbox is the SMTP identity — these send as team@tournamentsc.com,
a separate real mailbox, over its own connection (see `_connection()` below)
rather than reusing the support@ credentials.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _connection():
    return get_connection(
        backend=settings.EMAIL_BACKEND, host=settings.EMAIL_HOST, port=settings.EMAIL_PORT,
        username=settings.TEAM_EMAIL_HOST_USER, password=settings.TEAM_EMAIL_HOST_PASSWORD,
        use_ssl=settings.EMAIL_USE_SSL, use_tls=settings.EMAIL_USE_TLS)


def _send(template_stem, subject, to_email, ctx):
    ctx = {**ctx, 'site_url': settings.SITE_URL}
    text_body = render_to_string(f'emails/{template_stem}.txt', ctx)
    html_body = render_to_string(f'emails/{template_stem}.html', ctx)
    msg = EmailMultiAlternatives(subject, text_body, settings.TEAM_FROM_EMAIL, [to_email],
                                 connection=_connection())
    msg.attach_alternative(html_body, 'text/html')
    msg.send()


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_tournament_published_task(self, tournament_id):
    from .models import Tournament
    t = Tournament.objects.select_related('organizer__user', 'sport').filter(pk=tournament_id).first()
    if not t:
        return
    try:
        _send('tournament_published', f'"{t.name}" is now live', t.organizer.user.email, {'t': t})
    except Exception as exc:
        logger.warning('Published email failed for %s: %s', t.slug, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error('Giving up on published email for %s', t.slug)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_fixtures_created_task(self, tournament_id):
    from .models import Tournament
    t = Tournament.objects.select_related('organizer__user', 'sport').filter(pk=tournament_id).first()
    if not t:
        return
    try:
        _send('fixtures_created', f'Fixtures are ready for "{t.name}"', t.organizer.user.email, {'t': t})
    except Exception as exc:
        logger.warning('Fixtures-created email failed for %s: %s', t.slug, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error('Giving up on fixtures-created email for %s', t.slug)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_tournament_completed_task(self, tournament_id):
    from .models import Tournament
    t = Tournament.objects.select_related('organizer__user', 'sport').filter(pk=tournament_id).first()
    if not t:
        return
    try:
        _send('tournament_completed', f'"{t.name}" has wrapped up', t.organizer.user.email, {'t': t})
    except Exception as exc:
        logger.warning('Completed email failed for %s: %s', t.slug, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error('Giving up on completed email for %s', t.slug)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_player_welcome_task(self, player_profile_id, tournament_id):
    from accounts.models import PlayerProfile
    from .models import Tournament
    player = PlayerProfile.objects.select_related('user').filter(pk=player_profile_id).first()
    t = Tournament.objects.select_related('organizer__user', 'sport', 'venue').filter(pk=tournament_id).first()
    if not player or not t:
        return
    try:
        _send('player_welcome', f'You\'re in! Welcome to "{t.name}"', player.user.email,
             {'player': player, 't': t})
    except Exception as exc:
        logger.warning('Welcome email failed for player %s / %s: %s', player_profile_id, t.slug, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error('Giving up on welcome email for player %s / %s', player_profile_id, t.slug)
