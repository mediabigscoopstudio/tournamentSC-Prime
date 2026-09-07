from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.models import Group, Permission

from .models import OrganizerApplication, OrganizerProfile, PlayerProfile, User


# ======================================================================
# Sign-up  (one base, two role-scoped subclasses)
# ======================================================================
class BaseSignupForm(forms.ModelForm):
    """Shared account creation. Subclasses fix the signup intent, so the user is
    never asked to pick a role on a page they already navigated to by role."""

    signup_intent = User.INTENT_PLAYER  # overridden by subclasses

    password1 = forms.CharField(label='Password', widget=forms.PasswordInput, min_length=8)
    password2 = forms.CharField(label='Confirm password', widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ['first_name', 'email', 'phone_number']

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean_phone_number(self):
        phone = (self.cleaned_data.get('phone_number') or '').strip()
        if phone and User.objects.filter(phone_number=phone).exists():
            raise forms.ValidationError('An account with this phone number already exists.')
        return phone or None

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Passwords do not match.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        user.signup_intent = self.signup_intent
        # AbstractUser still carries a unique `username`. Sign-up collects only an
        # email, so derive a unique username here — without this every account after
        # the first collides on username=''.
        if not user.username:
            user.username = User.objects._make_username(user.email)
        # A public sign-up can never mint a platform administrator.
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
        return user


class PlayerSignupForm(BaseSignupForm):
    signup_intent = User.INTENT_PLAYER


class OrganizerSignupForm(BaseSignupForm):
    signup_intent = User.INTENT_ORGANIZER

    organization_name = forms.CharField(
        max_length=255, required=False, label='Organisation / club name')

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            OrganizerProfile.objects.update_or_create(
                user=user,
                defaults={'organization_name': self.cleaned_data.get('organization_name', '')})
        return user


# ======================================================================
# Login  (one credential check, three role-scoped gates)
# ======================================================================
class BaseLoginForm(forms.Form):
    """Resolves email-or-phone to an account and validates the password.

    Subclasses implement `check_role`, which is what keeps the three login
    pages from being interchangeable doors into the same building.
    """

    identifier = forms.CharField(label='Email or phone')
    password = forms.CharField(widget=forms.PasswordInput)

    wrong_door_message = 'This account cannot sign in here.'

    def check_role(self, user):
        raise NotImplementedError

    def clean(self):
        cleaned = super().clean()
        identifier = (cleaned.get('identifier') or '').strip()
        password = cleaned.get('password')
        if not (identifier and password):
            return cleaned

        # Resolve email or phone -> the account's email (the USERNAME_FIELD).
        email = identifier.lower()
        if '@' not in identifier:
            match = User.objects.filter(phone_number=identifier).first()
            email = match.email if match else None

        user = authenticate(username=email, password=password) if email else None
        if user is None:
            raise forms.ValidationError('Invalid credentials. Check your email/phone and password.')
        if user.is_suspended:
            raise forms.ValidationError('This account has been suspended. Contact the TournamentSC team.')
        if not self.check_role(user):
            raise forms.ValidationError(self.wrong_door_message)
        cleaned['user'] = user
        return cleaned


class PlayerLoginForm(BaseLoginForm):
    wrong_door_message = ('This is the player sign-in. Administrator accounts sign in '
                          'through the admin console.')

    def check_role(self, user):
        return not user.is_staff


class OrganizerLoginForm(BaseLoginForm):
    wrong_door_message = ('This is the organizer sign-in. Administrator accounts sign in '
                          'through the admin console.')

    def check_role(self, user):
        return not user.is_staff


class AdminLoginForm(BaseLoginForm):
    wrong_door_message = 'Invalid admin credentials, or this account is not an administrator.'

    def check_role(self, user):
        return user.is_staff


# ======================================================================
# Profiles / applications
# ======================================================================
class OrganizerApplicationForm(forms.ModelForm):
    class Meta:
        model = OrganizerApplication
        fields = ['affiliation', 'sports', 'reason']
        widgets = {
            'reason': forms.Textarea(attrs={'rows': 4,
                     'placeholder': 'Tell us what you plan to run and why.'}),
            'sports': forms.CheckboxSelectMultiple,
        }


class OrganizerProfileForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150, required=False, label='Contact name')

    class Meta:
        model = OrganizerProfile
        fields = ['organization_name', 'bio', 'profile_photo', 'cover_photo']
        widgets = {'bio': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user_id:
            self.fields['first_name'].initial = self.instance.user.first_name

    def save(self, commit=True):
        profile = super().save(commit=commit)
        name = self.cleaned_data.get('first_name')
        if name is not None and profile.user_id:
            profile.user.first_name = name
            profile.user.save(update_fields=['first_name'])
        return profile


class PlayerProfileForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150, required=False, label='Display name')

    class Meta:
        model = PlayerProfile
        fields = ['city', 'date_of_birth', 'gender', 'profile_photo', 'cover_photo', 'bio', 'rating',
                  'sports', 'emergency_contact_name', 'emergency_contact_phone']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'bio': forms.Textarea(attrs={'rows': 3}),
            'sports': forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user_id:
            self.fields['first_name'].initial = self.instance.user.first_name

    def save(self, commit=True):
        profile = super().save(commit=commit)
        name = self.cleaned_data.get('first_name')
        if name is not None and profile.user_id:
            profile.user.first_name = name
            profile.user.save(update_fields=['first_name'])
        return profile


# ======================================================================
# Admin-side forms (roles & permissions)
# ======================================================================
class RoleForm(forms.ModelForm):
    """A 'role' is a Django auth Group — reusing the framework rather than
    inventing a parallel permission system."""

    class Meta:
        model = Group
        fields = ['name', 'permissions']
        widgets = {'permissions': forms.SelectMultiple(attrs={'size': 18})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['permissions'].queryset = Permission.objects.select_related(
            'content_type').order_by('content_type__app_label', 'codename')
