"""Async Firebase Cloud Messaging push delivery.

Same shape as tournaments/tasks.py and support/tasks.py: a Celery task,
retried a few times on failure then logged and dropped, so a slow/unreachable
FCM endpoint never blocks the view/signal that triggered it. Fires from
accounts/signals.py's post_save receiver on Notification — every existing
Notification.push() call site in the codebase gets push delivery for free.
"""
import logging

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_fcm_push_task(self, notification_id):
    if not settings.USE_FCM_PUSH:
        return
    from .models import Notification, UserFCMToken

    notif = Notification.objects.select_related('recipient').filter(pk=notification_id).first()
    if not notif:
        return
    token = UserFCMToken.objects.filter(user=notif.recipient, is_active=True).first()
    if not token:
        return

    try:
        from firebase_admin import messaging
        messaging.send(messaging.Message(
            notification=messaging.Notification(title='TournamentSC', body=notif.message),
            data={'url': notif.url} if notif.url else None,
            token=token.token,
        ))
    except Exception as exc:
        logger.warning('FCM push failed for notification %s: %s', notification_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error('Giving up on FCM push for notification %s', notification_id)
