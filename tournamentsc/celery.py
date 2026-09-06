"""Celery app for TournamentSC.

Only used today for outbound email (support confirmations/status updates) so a
slow or unreachable SMTP server never blocks a web request, and sending many
emails at once (e.g. closing several tickets, or a future broadcast) is spread
across worker processes instead of piling up on one request thread.

Run a worker alongside the web process:

    celery -A tournamentsc worker -l info

In dev, CELERY_TASK_ALWAYS_EAGER (on by default whenever DEBUG=True) runs
tasks inline instead, so nothing extra needs to be running locally.
"""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tournamentsc.settings')

app = Celery('tournamentsc')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
