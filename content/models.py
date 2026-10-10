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
    TYPE_CAROUSEL = 'carousel'
    TYPE_SHORT = 'short'
    TYPE_FULL_VIDEO = 'full_video'
    CONTENT_TYPE_CHOICES = [
        (TYPE_PHOTO, 'Photo'),
        (TYPE_VIDEO, 'Video'),
        (TYPE_POST, 'Post'),
        (TYPE_CAROUSEL, 'Carousel'),
        (TYPE_SHORT, 'Short Video'),
        (TYPE_FULL_VIDEO, 'Full Video'),
    ]

    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='content_posts')
    tournament = models.ForeignKey('tournaments.Tournament', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='content_posts')
    team_author = models.ForeignKey('tournaments.Team', on_delete=models.CASCADE,
                                    null=True, blank=True, related_name='authored_content')
    organizer_author = models.ForeignKey('accounts.OrganizerProfile', on_delete=models.CASCADE,
                                         null=True, blank=True, related_name='authored_content')
    content_type = models.CharField(max_length=15, choices=CONTENT_TYPE_CHOICES, default=TYPE_POST)
    title = models.CharField(max_length=160, blank=True)
    description = models.TextField(max_length=500, blank=True)
    media_file = models.FileField(
        upload_to='content/media/', null=True, blank=True,
        validators=[FileExtensionValidator(CONTENT_MEDIA_EXTENSIONS), validate_content_media_size])
    aspect_ratio = models.CharField(max_length=10, blank=True, default='1/1')
    youtube_url = models.URLField(max_length=500, blank=True)
    video_duration = models.PositiveIntegerField(null=True, blank=True, help_text="Duration in seconds")
    tagged_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='tagged_in_content')

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

    @property
    def is_carousel(self):
        if self.content_type == self.TYPE_CAROUSEL:
            return True
        return self.media_items.count() > 1

    @property
    def is_video(self):
        if self.content_type in (self.TYPE_VIDEO, self.TYPE_SHORT, self.TYPE_FULL_VIDEO) or self.youtube_url:
            return True
        if self.media_file:
            ext = self.media_file.name.lower().split('.')[-1]
            return ext in ['mp4', 'mov', 'webm']
        return False

    @property
    def formatted_duration(self):
        if not self.video_duration:
            return ''
        mins = self.video_duration // 60
        secs = self.video_duration % 60
        return f"{mins}:{secs:02d}"

    @property
    def youtube_video_id(self):
        if not self.youtube_url:
            return ''
        import re
        patterns = [
            r'(?:v=|\/|vi=)([0-9A-Za-z_-]{11})',
            r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
            r'(?:embed\/)([0-9A-Za-z_-]{11})',
            r'(?:shorts\/)([0-9A-Za-z_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, self.youtube_url)
            if match:
                return match.group(1)
        return ''

    @property
    def youtube_embed_url(self):
        vid = self.youtube_video_id
        if vid:
            return f'https://www.youtube-nocookie.com/embed/{vid}'
        return ''

    @property
    def youtube_thumbnail_url(self):
        vid = self.youtube_video_id
        if vid:
            return f'https://img.youtube.com/vi/{vid}/hqdefault.jpg'
        return ''

    @property
    def primary_thumbnail_url(self):
        if self.youtube_url and self.youtube_thumbnail_url:
            return self.youtube_thumbnail_url
        if self.media_file:
            return self.media_file.url
        first_item = self.media_items.first()
        if first_item and first_item.media_file:
            return first_item.media_file.url
        return ''

    @property
    def all_media_items(self):
        items = list(self.media_items.all())
        if items:
            return items
        if self.media_file:
            return [{'media_file': self.media_file, 'is_video': self.is_video, 'order': 0}]
        return []


class ContentMedia(TimeStamped):
    content = models.ForeignKey(Content, on_delete=models.CASCADE, related_name='media_items')
    media_file = models.FileField(
        upload_to='content/media/',
        validators=[FileExtensionValidator(CONTENT_MEDIA_EXTENSIONS), validate_content_media_size]
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'created_at']

    def __str__(self):
        return f'Media #{self.order + 1} for Content #{self.content_id}'

    @property
    def is_video(self):
        if not self.media_file:
            return False
        ext = self.media_file.name.lower().split('.')[-1]
        return ext in ['mp4', 'mov', 'webm']


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


class ContentBookmark(TimeStamped):
    content = models.ForeignKey(Content, on_delete=models.CASCADE, related_name='bookmarks')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='content_bookmarks')

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['content', 'user'], name='uniq_content_user_bookmark'),
        ]

    def __str__(self):
        return f'{self.user.display_name} saved {self.content_id}'
