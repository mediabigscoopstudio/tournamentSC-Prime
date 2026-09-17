import os
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps


def process_team_logo(uploaded_file, size=500):
    """Validate a team-logo upload is square, then resize to size×size and
    convert to WebP. Returns a ContentFile ready to assign straight to the
    model field — nothing else needs to happen at the call site."""
    image = Image.open(uploaded_file)
    image = ImageOps.exif_transpose(image)  # respect phone-camera orientation
    width, height = image.size
    if width != height:
        raise ValidationError(
            f'Logo must be a square image (equal width and height) — this one is '
            f'{width}×{height}px.')
    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGBA')  # preserve transparency where present
    if (width, height) != (size, size):
        image = image.resize((size, size), Image.LANCZOS)
    buf = BytesIO()
    image.save(buf, format='WEBP', quality=90)
    name = os.path.splitext(uploaded_file.name)[0] + '.webp'
    return ContentFile(buf.getvalue(), name=name)
