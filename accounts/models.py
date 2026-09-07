from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse

from .managers import UserManager


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class User(AbstractUser):
    """Custom user. Email is the login identifier.

    Per the TRD there is no rigid role column controlling permissions —
    "organizer" and "player" are capabilities expressed by the presence of the
    two profile tables below, and "admin" maps to Django's is_staff. A soft
    `signup_intent` is kept only to route a new account to the right first-run
    flow.
    """

    INTENT_PLAYER = 'player'
    INTENT_ORGANIZER = 'organizer'
    INTENT_CHOICES = [(INTENT_PLAYER, 'Player'), (INTENT_ORGANIZER, 'Organizer')]

    email = models.EmailField('email address', unique=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True, unique=True)
    signup_intent = models.CharField(max_length=20, choices=INTENT_CHOICES, default=INTENT_PLAYER)
    is_verified = models.BooleanField(default=False, help_text='Admin-toggled verification badge.')
    is_suspended = models.BooleanField(default=False)
    suspended_reason = models.TextField(blank=True)
    fcm_notifications = models.BooleanField(
        default=True, help_text='Deliver notifications as a push notification, not just in-app.')

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []  # email + password only

    objects = UserManager()

    def __str__(self):
        return self.email

    @property
    def display_name(self):
        return (self.get_full_name() or '').strip() or self.username or self.email.split('@')[0]

    @property
    def is_platform_admin(self):
        return self.is_staff

    @property
    def is_approved_organizer(self):
        prof = getattr(self, 'organizer_profile', None)
        return bool(prof and prof.is_approved and not self.is_suspended)

    @property
    def has_organizer_profile(self):
        return hasattr(self, 'organizer_profile')


class OrganizerProfile(TimeStamped):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='organizer_profile')
    organization_name = models.CharField(max_length=255, blank=True)
    bio = models.TextField(blank=True)
    profile_photo = models.ImageField(upload_to='organizers/', null=True, blank=True)
    cover_photo = models.ImageField(upload_to='organizers/covers/', null=True, blank=True)
    is_approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='organizers_approved')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    def __str__(self):
        return f'Organizer: {self.user.display_name}'

    @property
    def completion_percentage(self):
        checks = [
            bool(self.user.email),
            bool(self.user.first_name),
            bool(self.bio),
            bool(self.profile_photo),
            bool(self.organization_name),
        ]
        return round(sum(checks) / len(checks) * 100)

    @property
    def is_profile_complete(self):
        return self.completion_percentage >= 80


class PlayerProfile(TimeStamped):
    GENDER_CHOICES = [('M', 'Male'), ('F', 'Female'), ('O', 'Other'), ('U', 'Prefer not to say')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='player_profile')
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, default='U')
    city = models.CharField(max_length=120, blank=True)
    profile_photo = models.ImageField(upload_to='players/', null=True, blank=True)
    cover_photo = models.ImageField(upload_to='players/covers/', null=True, blank=True)
    bio = models.TextField(blank=True)
    rating = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Skill rating (e.g. a chess Elo). Shown on rating-based standings.')
    sports = models.ManyToManyField('tournaments.Sport', blank=True, related_name='interested_players')
    emergency_contact_name = models.CharField(max_length=120, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f'Player: {self.user.display_name}'

    def get_absolute_url(self):
        return reverse('player_public', args=[self.pk])

    @property
    def completion_percentage(self):
        checks = [
            bool(self.user.email),
            bool(self.user.first_name),
            bool(self.bio),
            bool(self.profile_photo),
            self.sports.exists(),
        ]
        return round(sum(checks) / len(checks) * 100)

    @property
    def is_profile_complete(self):
        return self.completion_percentage >= 80


class OrganizerApplication(TimeStamped):
    STATUS_PENDING = 'PENDING'
    STATUS_APPROVED = 'APPROVED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='organizer_applications')
    affiliation = models.CharField(max_length=255)
    sports = models.ManyToManyField('tournaments.Sport', blank=True, related_name='organizer_applications')
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='applications_reviewed')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.display_name} — {self.status}'


class Follow(TimeStamped):
    """A user following a tournament (the reference's "Follow" CTA).

    Followers are the audience for result notifications — the one place the
    platform can reach someone who is neither a player nor the organizer.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='follows')
    tournament = models.ForeignKey('tournaments.Tournament', on_delete=models.CASCADE,
                                   related_name='followers')

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'tournament'], name='uniq_user_tournament_follow'),
        ]

    def __str__(self):
        return f'{self.user.display_name} follows {self.tournament_id}'


class Notification(TimeStamped):
    """Generic in-app notification (NOTIF-01). Any feature can create one."""
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='notifications')
    verb = models.CharField(max_length=60, default='update')
    message = models.CharField(max_length=300)
    url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'To {self.recipient_id}: {self.message[:40]}'

    @classmethod
    def push(cls, recipient, message, url='', verb='update'):
        return cls.objects.create(recipient=recipient, message=message, url=url, verb=verb)


class UserFCMToken(TimeStamped):
    """A browser/device's Firebase Cloud Messaging token, for push delivery of
    Notification rows. One token per user — the most recently registered
    device wins, matching how a single browser session re-registers itself."""
    DEVICE_CHOICES = [('web', 'Web'), ('mobile', 'Mobile'), ('desktop', 'Desktop')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='fcm_token')
    token = models.TextField(unique=True)
    device_type = models.CharField(max_length=10, choices=DEVICE_CHOICES, default='web')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.user.display_name} ({self.device_type})'


class AuditLog(TimeStamped):
    """Records moderation/admin actions (ADMIN-03)."""
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                              related_name='audit_actions')
    action = models.CharField(max_length=120)
    target = models.CharField(max_length=255, blank=True)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.action} · {self.target}'

    @classmethod
    def record(cls, actor, action, target='', detail=''):
        return cls.objects.create(actor=actor, action=action, target=str(target), detail=detail)
