from django.core.exceptions import ValidationError

# The first FileField in the project open to arbitrary player/organizer
# uploads (existing FileFields — Tournament.banner_image, Highlight.image,
# MediaAsset.file — are organizer/admin-only). Keep abuse-prevention minimal:
# extension whitelist + a size cap, no transformation.
CONTENT_MEDIA_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'mp4', 'mov', 'webm']
CONTENT_MEDIA_MAX_BYTES = 100 * 1024 * 1024  # 100MB


def validate_content_media_size(file):
    if file.size > CONTENT_MEDIA_MAX_BYTES:
        raise ValidationError(
            f'File is too large ({file.size / 1024 / 1024:.1f}MB). Max size is '
            f'{CONTENT_MEDIA_MAX_BYTES / 1024 / 1024:.0f}MB.')
