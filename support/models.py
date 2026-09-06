"""Customer support tickets raised by players or organizers from their dashboard.

One form, two subject vocabularies — the role a ticket was raised under decides
which list of subjects applied, but both land in the same table so the admin
console can manage them with the existing generic resource framework.
"""
from django.conf import settings
from django.db import models

from accounts.models import TimeStamped

ROLE_PLAYER = 'PLAYER'
ROLE_ORGANIZER = 'ORGANIZER'
ROLE_CHOICES = [(ROLE_PLAYER, 'Player'), (ROLE_ORGANIZER, 'Organizer')]

PLAYER_SUBJECTS = [
    'Registration issue',
    'Registration not showing',
    'Profile information correction',
    'Team joining issue',
    'Wrong team assignment',
    'Match/fixture not found',
    'Match schedule change',
    'Wrong opponent or bracket',
    'Incorrect match score',
    'Match result not updated',
    'Live score not updating',
    'Notification not received',
    'Tournament information request',
    'Payment/registration fee issue',
    'Tournament status issue',
]

ORGANIZER_SUBJECTS = [
    'Tournament creation issue',
    'Tournament setup issue',
    'Registration management',
    'Player management',
    'Team management',
    'Fixture scheduling issue',
    'Bracket management',
    'Match assignment issue',
    'Score/result management',
    'Live score issue',
    'Tournament standings issue',
    'Payment/payout issue',
    'Venue management',
    'Notification issue',
    'Tournament cancellation or postponement',
]

SUBJECT_CHOICES = [(s, s) for s in PLAYER_SUBJECTS + ORGANIZER_SUBJECTS]


class SupportTicket(TimeStamped):
    STATUS_OPEN = 'OPEN'
    STATUS_CLOSED = 'CLOSED'
    STATUS_CHOICES = [(STATUS_OPEN, 'Open'), (STATUS_CLOSED, 'Closed')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='support_tickets')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField()
    subject = models.CharField(max_length=100, choices=SUBJECT_CHOICES)
    message = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(
        blank=True, help_text='Shown to the requester in the closing email.')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.ref} · {self.subject}'

    @property
    def ref(self):
        return f'SUP-{self.pk:06d}' if self.pk else 'SUP-NEW'


class SupportAttachment(TimeStamped):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE,
                               related_name='attachments')
    file = models.FileField(upload_to='support/%Y/%m/')

    def __str__(self):
        return self.file.name
