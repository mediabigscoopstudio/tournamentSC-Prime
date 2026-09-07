from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.urls import reverse

from accounts.models import TimeStamped
from .validators import CONTENT_MEDIA_EXTENSIONS, validate_content_media_size


class Content(TimeStamped):
    """A player/organizer-posted photo, video, or text update — optionally
    tied to a tournament. Counters are flat denormalized fields, same
    approach as tournaments.Highlight.view_count — no per-view row."""
    TYPE_PHOTO = 'photo'
    TYPE_VIDEO = 'video'
    TYPE_POST = 'post'
    CONTENT_TYPE_CHOICES = [(TYPE_PHOTO, 'Photo'), (TYPE_VIDEO, 'Video'), (TYPE_POST, 'Post')]

    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='content_posts')
    tournament = models.ForeignKey('tournaments.Tournament', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='content_posts')
    content_type = models.CharField(max_length=10, choices=CONTENT_TYPE_CHOICES, default=TYPE_POST)
    title = models.CharField(max_length=160, blank=True)
    description = models.TextField(max_length=500, blank=True)
    media_file = models.FileField(
        upload_to='content/media/', null=True, blank=True,
        validators=[FileExtensionValidator(CONTENT_MEDIA_EXTENSIONS), validate_content_media_size])

    view_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)
    comment_count = models.PositiveIntegerField(default=0)
    share_count = models.PositiveIntegerField(default=0)

    is_published = models.BooleanField(default=True)
    is_removed = models.BooleanField(default=False)
    removal_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['tournament']),
        ]

    def __str__(self):
        return self.title or f'{self.get_content_type_display()} by {self.creator.display_name}'

    def get_absolute_url(self):
        return reverse('content_detail', args=[self.pk])

    @property
    def is_visible(self):
        return self.is_published and not self.is_removed


class ContentLike(TimeStamped):
    content = models.ForeignKey(Content, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='content_likes')

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['content', 'user'], name='uniq_content_user_like'),
        ]

    def __str__(self):
        return f'{self.user.display_name} likes {self.content_id}'


class ContentComment(TimeStamped):
    content = models.ForeignKey(Content, on_delete=models.CASCADE, related_name='comments')
    commenter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='content_comments')
    text = models.CharField(max_length=1000)
    is_removed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.commenter.display_name}: {self.text[:40]}'


class ContentShare(TimeStamped):
    SHARE_LINK = 'link'
    SHARE_SOCIAL = 'social'
    SHARE_INTERNAL = 'internal'
    SHARE_TYPE_CHOICES = [(SHARE_LINK, 'Link'), (SHARE_SOCIAL, 'Social media'), (SHARE_INTERNAL, 'Internal')]

    content = models.ForeignKey(Content, on_delete=models.CASCADE, related_name='shares')
    shared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='content_shares')
    share_type = models.CharField(max_length=10, choices=SHARE_TYPE_CHOICES, default=SHARE_LINK)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.shared_by.display_name} shared {self.content_id}'


class Achievement(models.Model):
    """A criteria-based badge (e.g. {"wins": 10}). Awarded by
    content.tasks.check_and_award_achievements_task."""
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=8, default='🏅', help_text='An emoji, matching Sport.icon\'s convention.')
    criteria = models.JSONField(default=dict, help_text='e.g. {"wins": 10} or {"tournaments": 5}')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.icon} {self.name}'


class UserAchievement(TimeStamped):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='achievements')
    achievement = models.ForeignKey(Achievement, on_delete=models.CASCADE, related_name='awarded_to')

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'achievement'], name='uniq_user_achievement'),
        ]

    def __str__(self):
        return f'{self.user.display_name} earned {self.achievement.name}'
