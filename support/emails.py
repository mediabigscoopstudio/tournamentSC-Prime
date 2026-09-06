"""Transactional emails for support tickets.

Sent synchronously from the view/admin action — the project has no task queue,
and every other notification path in this codebase is request-synchronous too.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def _send(template_stem, subject, to_email, ctx):
    ctx = {**ctx, 'site_url': settings.SITE_URL}
    text_body = render_to_string(f'emails/{template_stem}.txt', ctx)
    html_body = render_to_string(f'emails/{template_stem}.html', ctx)
    msg = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [to_email])
    msg.attach_alternative(html_body, 'text/html')
    msg.send(fail_silently=False)


def send_ticket_confirmation(ticket):
    _send('support_confirmation', f'We received your request ({ticket.ref})',
         ticket.email, {'ticket': ticket})


def send_ticket_status_update(ticket):
    _send('support_status_update', f'Your request {ticket.ref} has been closed',
         ticket.email, {'ticket': ticket})
