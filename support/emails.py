"""Public entry points for support-ticket emails.

Both just enqueue a Celery task (see tasks.py) — actual sending happens off
the request thread. Enqueuing itself is wrapped so that even a broker outage
can't take down ticket submission/closing; it only means the email is skipped
and logged instead of queued.
"""
import logging

from .tasks import send_ticket_confirmation_task, send_ticket_status_update_task

logger = logging.getLogger(__name__)


def send_ticket_confirmation(ticket):
    try:
        send_ticket_confirmation_task.delay(ticket.pk)
    except Exception:
        logger.exception('Could not enqueue confirmation email for %s', ticket.ref)


def send_ticket_status_update(ticket):
    try:
        send_ticket_status_update_task.delay(ticket.pk)
    except Exception:
        logger.exception('Could not enqueue status-update email for %s', ticket.ref)
