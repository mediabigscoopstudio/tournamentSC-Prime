from django import forms

from .models import Content, ContentComment


class ContentUploadForm(forms.ModelForm):
    class Meta:
        model = Content
        fields = ['content_type', 'title', 'description', 'media_file', 'tournament']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def clean(self):
        cleaned = super().clean()
        content_type = cleaned.get('content_type')
        if content_type in (Content.TYPE_PHOTO, Content.TYPE_VIDEO) and not cleaned.get('media_file'):
            raise forms.ValidationError('A photo/video post needs a media file.')
        return cleaned


class ContentCommentForm(forms.ModelForm):
    class Meta:
        model = ContentComment
        fields = ['text']
        widgets = {'text': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Add a comment…'})}
