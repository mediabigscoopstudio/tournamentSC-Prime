"""Forms used only inside the platform-admin console."""
from django import forms
from django.contrib.auth.models import Group

from accounts.models import OrganizerProfile, PlayerProfile, User
from content.models import Achievement, Content, ContentComment

from support.models import SupportTicket

from .models import Announcement, MediaAsset, News, SiteSetting


class AdminUserForm(forms.ModelForm):
    """Full account management, including creating other administrators.

    This is the *only* place `is_staff` can be granted — the public sign-up
    forms hard-code it to False.
    """

    password1 = forms.CharField(
        label='Password', widget=forms.PasswordInput, required=False, min_length=8,
        help_text='Leave blank to keep the current password.')
    is_platform_admin = forms.BooleanField(
        label='Platform administrator', required=False,
        help_text='Grants access to this admin console. Nothing else does.')
    roles = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(), required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone_number', 'is_verified',
                  'is_suspended', 'suspended_reason', 'is_active']
        widgets = {'suspended_reason': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop('request_user', None)
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['is_platform_admin'].initial = self.instance.is_staff
            self.fields['roles'].initial = self.instance.groups.all()
        else:
            self.fields['password1'].required = True
            self.fields['password1'].help_text = 'Minimum 8 characters.'

        # Only a superuser may mint or demote administrators.
        if not (self.request_user and self.request_user.is_superuser):
            self.fields['is_platform_admin'].disabled = True
            self.fields['roles'].disabled = True

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        qs = User.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean_phone_number(self):
        phone = (self.cleaned_data.get('phone_number') or '').strip()
        if not phone:
            return None
        qs = User.objects.filter(phone_number=phone)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('An account with this phone number already exists.')
        return phone

    def clean(self):
        cleaned = super().clean()
        # Guard against an admin locking themselves out of the console.
        if (self.instance.pk and self.request_user and self.instance.pk == self.request_user.pk):
            if cleaned.get('is_suspended') or not cleaned.get('is_active', True):
                raise forms.ValidationError('You cannot suspend or deactivate your own account.')
            if self.instance.is_staff and not cleaned.get('is_platform_admin', True):
                raise forms.ValidationError('You cannot remove your own administrator access.')
        if self.instance.pk and self.instance.is_superuser and not cleaned.get(
                'is_platform_admin', True):
            raise forms.ValidationError('A superuser must remain a platform administrator.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        if not user.username:
            user.username = User.objects._make_username(user.email)
        if not self.fields['is_platform_admin'].disabled:
            user.is_staff = self.cleaned_data.get('is_platform_admin', False)
        password = self.cleaned_data.get('password1')
        if password:
            user.set_password(password)
        if commit:
            user.save()
            if not self.fields['roles'].disabled:
                user.groups.set(self.cleaned_data.get('roles') or [])
            # Every non-admin account has a player profile (the baseline capability).
            if not user.is_staff:
                PlayerProfile.objects.get_or_create(user=user)
        return user


class OrganizerProfileAdminForm(forms.ModelForm):
    class Meta:
        model = OrganizerProfile
        fields = ['user', 'organization_name', 'bio', 'is_approved', 'rejection_reason']
        widgets = {'bio': forms.Textarea(attrs={'rows': 3}),
                   'rejection_reason': forms.Textarea(attrs={'rows': 2})}


class PlayerProfileAdminForm(forms.ModelForm):
    class Meta:
        model = PlayerProfile
        fields = ['user', 'date_of_birth', 'gender', 'city', 'profile_photo', 'bio', 'rating',
                  'sports', 'emergency_contact_name', 'emergency_contact_phone']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'bio': forms.Textarea(attrs={'rows': 3}),
            'sports': forms.CheckboxSelectMultiple,
        }


class NewsForm(forms.ModelForm):
    class Meta:
        model = News
        fields = ['title', 'slug', 'summary', 'body', 'cover_image', 'sport', 'tournament',
                  'status', 'published_at', 'is_archived']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 10}),
            'summary': forms.Textarea(attrs={'rows': 2}),
            'published_at': forms.DateTimeInput(attrs={'type': 'datetime-local'},
                                                format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].required = False
        self.fields['slug'].help_text = 'Leave blank to generate from the title.'
        self.fields['published_at'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S',
                                                     '%Y-%m-%d %H:%M']

    def clean(self):
        cleaned = super().clean()
        # Publishing without a date would leave the article invisible on the public
        # site — silently wrong. Stamp it instead.
        if cleaned.get('status') == News.STATUS_PUBLISHED and not cleaned.get('published_at'):
            from django.utils import timezone
            cleaned['published_at'] = timezone.now()
        return cleaned


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['title', 'message', 'level', 'show_on_site', 'starts_at', 'ends_at',
                  'status', 'published_at', 'is_archived']
        widgets = {
            'message': forms.Textarea(attrs={'rows': 3}),
            'starts_at': forms.DateTimeInput(attrs={'type': 'datetime-local'},
                                             format='%Y-%m-%dT%H:%M'),
            'ends_at': forms.DateTimeInput(attrs={'type': 'datetime-local'},
                                           format='%Y-%m-%dT%H:%M'),
            'published_at': forms.DateTimeInput(attrs={'type': 'datetime-local'},
                                                format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        fmts = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
        for name in ('starts_at', 'ends_at', 'published_at'):
            self.fields[name].input_formats = fmts

    def clean(self):
        cleaned = super().clean()
        starts, ends = cleaned.get('starts_at'), cleaned.get('ends_at')
        if starts and ends and ends < starts:
            self.add_error('ends_at', 'The end time cannot be before the start time.')
        if cleaned.get('status') == Announcement.STATUS_PUBLISHED and not cleaned.get('published_at'):
            from django.utils import timezone
            cleaned['published_at'] = timezone.now()
        return cleaned


class MediaAssetForm(forms.ModelForm):
    class Meta:
        model = MediaAsset
        fields = ['title', 'kind', 'file', 'external_url', 'alt_text', 'tournament',
                  'is_archived']

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('file') and not cleaned.get('external_url'):
            raise forms.ValidationError('Upload a file or provide an external URL.')
        return cleaned


class AdminSupportTicketForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ['status', 'resolution_note']
        widgets = {'resolution_note': forms.Textarea(attrs={'rows': 4})}


class AdminContentForm(forms.ModelForm):
    class Meta:
        model = Content
        fields = ['creator', 'tournament', 'content_type', 'title', 'description', 'media_file',
                  'is_published', 'is_removed', 'removal_reason']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class AdminContentCommentForm(forms.ModelForm):
    class Meta:
        model = ContentComment
        fields = ['text', 'is_removed']
        widgets = {'text': forms.Textarea(attrs={'rows': 3})}


class AchievementForm(forms.ModelForm):
    class Meta:
        model = Achievement
        fields = ['name', 'description', 'icon', 'criteria', 'is_active']
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}


class SiteSettingForm(forms.ModelForm):
    class Meta:
        model = SiteSetting
        fields = ['site_name', 'tagline', 'support_email', 'contact_phone', 'meta_description',
                  'allow_player_registration', 'allow_organizer_registration',
                  'auto_approve_organizers', 'live_score_poll_seconds', 'featured_limit']
        widgets = {'meta_description': forms.Textarea(attrs={'rows': 2})}

    def clean_live_score_poll_seconds(self):
        value = self.cleaned_data['live_score_poll_seconds']
        if value < 5:
            raise forms.ValidationError('Polling faster than every 5 seconds will hammer the server.')
        return value
