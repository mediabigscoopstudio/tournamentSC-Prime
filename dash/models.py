"""Platform-content models owned by the admin application.

These exist purely so the platform administrator has first-class resources to
manage from the custom dashboard (News, Announcements, Media, Settings). They
are deliberately additive — no existing model in `accounts` or `tournaments` is
touched.
"""
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import TimeStamped


class PublishableQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Publishable.STATUS_PUBLISHED, is_archived=False,
                           published_at__isnull=False, published_at__lte=timezone.now())


class Publishable(TimeStamped):
    """Shared publish/archive lifecycle: Draft -> Published -> Archived."""

    STATUS_DRAFT = 'DRAFT'
    STATUS_PUBLISHED = 'PUBLISHED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_PUBLISHED, 'Published')]

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='+')

    objects = PublishableQuerySet.as_manager()

    class Meta:
        abstract = True

    @property
    def is_live(self):
        return (self.status == self.STATUS_PUBLISHED and not self.is_archived
                and self.published_at is not None and self.published_at <= timezone.now())

    def publish(self):
        self.status = self.STATUS_PUBLISHED
        self.published_at = self.published_at or timezone.now()
        self.is_archived = False
        self.save(update_fields=['status', 'published_at', 'is_archived', 'updated_at'])

    def unpublish(self):
        self.status = self.STATUS_DRAFT
        self.save(update_fields=['status', 'updated_at'])

    def archive(self):
        self.is_archived = True
        self.save(update_fields=['is_archived', 'updated_at'])

    def restore(self):
        self.is_archived = False
        self.save(update_fields=['is_archived', 'updated_at'])


class News(Publishable):
    """Editorial articles shown on the public site."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    body = models.TextField()
    cover_image = models.ImageField(upload_to='news/', null=True, blank=True)
    sport = models.ForeignKey('tournaments.Sport', on_delete=models.SET_NULL, null=True,
                              blank=True, related_name='news')
    tournament = models.ForeignKey('tournaments.Tournament', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='news')

    class Meta:
        ordering = ['-published_at', '-created_at']
        verbose_name_plural = 'news'

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:200] or 'news'
            candidate, i = base, 1
            while News.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                i += 1
                candidate = f'{base}-{i}'
            self.slug = candidate
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('news_detail', args=[self.slug])


class Announcement(Publishable):
    """Short site-wide banner notices (maintenance, new season, etc.)."""

    LEVEL_CHOICES = [('info', 'Info'), ('success', 'Success'),
                     ('warning', 'Warning'), ('error', 'Critical')]

    title = models.CharField(max_length=160)
    message = models.TextField(max_length=600)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='info')
    show_on_site = models.BooleanField(default=True, help_text='Display as a banner on the public site.')
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def is_current(self):
        if not self.is_live or not self.show_on_site:
            return False
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


class MediaAsset(TimeStamped):
    """A central library of uploaded files the admin can browse and reuse."""

    KIND_CHOICES = [('image', 'Image'), ('video', 'Video link'), ('document', 'Document')]

    title = models.CharField(max_length=160)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='image')
    file = models.FileField(upload_to='library/', null=True, blank=True)
    external_url = models.URLField(blank=True, help_text='Use for a YouTube or externally hosted asset.')
    alt_text = models.CharField(max_length=200, blank=True)
    tournament = models.ForeignKey('tournaments.Tournament', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='media_assets')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='media_uploads')
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def url(self):
        if self.file:
            return self.file.url
        return self.external_url


class SiteSetting(models.Model):
    """Singleton row holding site + application configuration.

    Admin-only. Read anywhere via `SiteSetting.load()`.
    """

    # --- Site settings ---
    site_name = models.CharField(max_length=80, default='TournamentSC')
    tagline = models.CharField(max_length=160, default='Play. Compete. Conquer.')
    support_email = models.EmailField(default='support@tournamentsc.com')
    contact_phone = models.CharField(max_length=32, blank=True)
    meta_description = models.CharField(
        max_length=300,
        default='The live home for local, multi-sport tournaments — discover, follow, and run them in one place.')

    # --- Application settings ---
    allow_player_registration = models.BooleanField(
        default=True, help_text='Turn off to close new player sign-ups.')
    allow_organizer_registration = models.BooleanField(
        default=True, help_text='Turn off to close new organizer sign-ups.')
    auto_approve_organizers = models.BooleanField(
        default=False, help_text='Approve organizer applications without manual review.')
    live_score_poll_seconds = models.PositiveIntegerField(default=12)
    featured_limit = models.PositiveIntegerField(default=5)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='+')

    class Meta:
        verbose_name = 'site settings'
        verbose_name_plural = 'site settings'

    def __str__(self):
        return self.site_name

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
