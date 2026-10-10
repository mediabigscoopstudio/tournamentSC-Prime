from django import forms

from .models import Content, ContentComment


class ContentUploadForm(forms.ModelForm):
    content_type = forms.CharField(required=False)
    aspect_ratio = forms.CharField(required=False, initial='1/1')
    media_file = forms.FileField(required=False)
    youtube_url = forms.URLField(required=False)
    video_duration = forms.IntegerField(required=False)

    class Meta:
        model = Content
        fields = ['content_type', 'title', 'description', 'media_file', 'aspect_ratio', 'youtube_url', 'video_duration', 'tournament']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def clean(self):
        cleaned = super().clean()
        content_type = cleaned.get('content_type')
        has_media = bool(
            cleaned.get('media_file') or 
            (self.files and self.files.getlist('media_files')) or 
            cleaned.get('youtube_url')
        )
        if content_type in (Content.TYPE_PHOTO, Content.TYPE_VIDEO, Content.TYPE_CAROUSEL, Content.TYPE_SHORT, Content.TYPE_FULL_VIDEO) and not has_media:
            raise forms.ValidationError('Please select or upload media, or enter a YouTube video URL.')
        return cleaned


class ContentCommentForm(forms.ModelForm):
    class Meta:
        model = ContentComment
        fields = ['text']
        widgets = {'text': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Add a comment…'})}
