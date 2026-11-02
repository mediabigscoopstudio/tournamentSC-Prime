from django import forms

from .models import Content, ContentComment
from .validators import CONTENT_MEDIA_EXTENSIONS, CONTENT_MEDIA_MAX_BYTES


class ContentUploadForm(forms.ModelForm):
    content_type = forms.CharField(required=False)
    aspect_ratio = forms.CharField(required=False, initial='1/1')
    media_file = forms.FileField(required=False)
    youtube_url = forms.CharField(required=False)
    video_duration = forms.IntegerField(required=False)

    class Meta:
        model = Content
        fields = ['content_type', 'title', 'description', 'media_file', 'aspect_ratio', 'youtube_url', 'video_duration', 'tournament']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def clean_youtube_url(self):
        url = self.cleaned_data.get('youtube_url', '').strip()
        if url:
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            from django.core.validators import URLValidator
            validator = URLValidator()
            validator(url)
        return url

    def clean(self):
        cleaned = super().clean()
        content_type = cleaned.get('content_type')
        youtube_url = cleaned.get('youtube_url')

        # Gather files from both single media_file and multiple media_files
        all_files = []
        if cleaned.get('media_file'):
            all_files.append(cleaned['media_file'])
        if self.files:
            if hasattr(self.files, 'getlist'):
                m_files = self.files.getlist('media_files')
            else:
                m_raw = self.files.get('media_files')
                m_files = m_raw if isinstance(m_raw, list) else ([m_raw] if m_raw else [])
            for f in m_files:
                if f and f not in all_files:
                    all_files.append(f)
            if not all_files and self.files.get('media_file'):
                all_files.append(self.files['media_file'])

        # Validate each uploaded file
        for f in all_files:
            ext = f.name.lower().split('.')[-1]
            if ext not in CONTENT_MEDIA_EXTENSIONS:
                self.add_error('media_file', f'Unsupported file format (.{ext}). Allowed formats: {", ".join(CONTENT_MEDIA_EXTENSIONS)}.')
            if f.size > CONTENT_MEDIA_MAX_BYTES:
                self.add_error('media_file', f'File "{f.name}" is too large ({f.size / 1024 / 1024:.1f}MB). Maximum allowed size is {CONTENT_MEDIA_MAX_BYTES // (1024 * 1024)}MB.')

        has_media = bool(all_files or youtube_url)
        if content_type in (Content.TYPE_PHOTO, Content.TYPE_VIDEO, Content.TYPE_CAROUSEL, Content.TYPE_SHORT, Content.TYPE_FULL_VIDEO) and not has_media:
            raise forms.ValidationError('Please select or upload media, or enter a YouTube video URL.')

        # If youtube_url is provided, automatically set type to full_video and aspect_ratio to 16/9
        if youtube_url:
            cleaned['content_type'] = Content.TYPE_FULL_VIDEO
            cleaned['aspect_ratio'] = '16/9'

        return cleaned


class ContentCommentForm(forms.ModelForm):
    class Meta:
        model = ContentComment
        fields = ['text']
        widgets = {'text': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Add a comment…'})}
