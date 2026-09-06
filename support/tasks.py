"""Async email delivery for support tickets.

Runs on a Celery worker (or inline in dev, see CELERY_TASK_ALWAYS_EAGER) so a
slow or unreachable SMTP server never blocks the request that raised or closed
a ticket, and sending many of these at once doesn't serialize on one thread.
A failed send is retried a few times before being logged and dropped — it
never bubbles back up to whoever triggered it.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _send(template_stem, subject, to_email, ctx):
    ctx = {**ctx, 'site_url': settings.SITE_URL}
    text_body = render_to_string(f'emails/{template_stem}.txt', ctx)
    html_body = render_to_string(f'emails/{template_stem}.html', ctx)
    msg = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [to_email])
    msg.attach_alternative(html_body, 'text/html')
    msg.send()


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_ticket_confirmation_task(self, ticket_id):
    from .models import SupportTicket
    ticket = SupportTicket.objects.filter(pk=ticket_id).first()
    if not ticket:
        return
    try:
        _send('support_confirmation', f'We received your request ({ticket.ref})',
             ticket.email, {'ticket': ticket})
    except Exception as exc:
        logger.warning('Support confirmation email failed for %s: %s', ticket.ref, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error('Giving up on confirmation email for %s', ticket.ref)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_ticket_status_update_task(self, ticket_id):
    from .models import SupportTicket
    ticket = SupportTicket.objects.filter(pk=ticket_id).first()
    if not ticket:
        return
    try:
        _send('support_status_update', f'Your request {ticket.ref} has been closed',
             ticket.email, {'ticket': ticket})
    except Exception as exc:
        logger.warning('Support status-update email failed for %s: %s', ticket.ref, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error('Giving up on status-update email for %s', ticket.ref)
