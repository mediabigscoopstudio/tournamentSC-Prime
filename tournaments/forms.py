from django import forms

from .models import (EventCategory, Fixture, Highlight, IndividualRegistration, Sport, Team,
                     TeamMembership, Tournament, TournamentTeamEntry, Venue)
from .utils import is_probable_google_form_url, is_probable_youtube_url


class TournamentForm(forms.ModelForm):
    class Meta:
        model = Tournament
        fields = ['name', 'sport', 'format', 'draw_category', 'description', 'rules', 'venue', 'city',
                  'start_date', 'end_date', 'registration_deadline',
                  'banner_image', 'entry_fee', 'prize_pool', 'max_participants', 'youtube_url',
                  'registration_form_url']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'rules': forms.Textarea(attrs={'rows': 3,
                     'placeholder': 'e.g. Best of 3 games to 21. Ties settled by a deciding point.'}),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'registration_deadline': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        self.locked = kwargs.pop('locked', False)  # after fixtures exist
        super().__init__(*args, **kwargs)
        self.fields['venue'].empty_label = 'No venue set'
        self.fields['registration_form_url'].label = 'Google Form Registration Link'
        self.fields['draw_category'].required = False
        if self.locked:
            for f in ('sport', 'format', 'draw_category'):
                self.fields[f].disabled = True

    def clean_prize_pool(self):
        value = self.cleaned_data.get('prize_pool')
        if value is not None and value < 0:
            raise forms.ValidationError('A prize pool cannot be negative.')
        return value

    def clean(self):
        cleaned = super().clean()
        sport = cleaned.get('sport')
        fmt = cleaned.get('format')
        if sport and fmt and fmt not in sport.allowed_formats:
            self.add_error('format',
                           f'{sport.name} cannot run as this format. '
                           f'Allowed: {", ".join(sport.allowed_formats)}.')
        start, end = cleaned.get('start_date'), cleaned.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'End date cannot be before start date.')
        url = cleaned.get('youtube_url')
        if url and not is_probable_youtube_url(url):
            self.add_error('youtube_url', 'Enter a valid youtube.com or youtu.be URL.')
        form_url = cleaned.get('registration_form_url')
        if form_url and not is_probable_google_form_url(form_url):
            self.add_error('registration_form_url',
                           'Enter a valid Google Form link (forms.gle or docs.google.com/forms).')
        return cleaned


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ['name', 'logo']


class TeamMemberForm(forms.ModelForm):
    class Meta:
        model = TeamMembership
        fields = ['team', 'player', 'display_name', 'role', 'jersey_number', 'is_approved']


class IndividualEntryForm(forms.ModelForm):
    class Meta:
        model = IndividualRegistration
        fields = ['display_name', 'event_category', 'bib_number', 'rating', 'phone_number', 'seed']

    def __init__(self, *args, tournament=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tournament is not None:
            self.fields['event_category'].queryset = tournament.categories.all()
            if not tournament.categories.exists():
                self.fields.pop('event_category', None)
            if tournament.sport.slug == 'chess':
                self.fields['rating'].label = 'FIDE rating'
                self.fields.pop('bib_number', None)
            else:
                self.fields.pop('rating', None)

    def clean_display_name(self):
        name = (self.cleaned_data.get('display_name') or '').strip()
        if not name:
            raise forms.ValidationError('Enter a name for this entrant.')
        return name


class FixtureScheduleForm(forms.ModelForm):
    class Meta:
        model = Fixture
        fields = ['scheduled_time', 'venue_detail', 'status']
        widgets = {'scheduled_time': forms.DateTimeInput(attrs={'type': 'datetime-local'},
                                                         format='%Y-%m-%dT%H:%M')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['scheduled_time'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S',
                                                       '%Y-%m-%d %H:%M']


class HighlightForm(forms.ModelForm):
    class Meta:
        model = Highlight
        fields = ['title', 'description', 'image', 'youtube_url', 'best_performer',
                  'duration_seconds']
        widgets = {'description': forms.Textarea(attrs={'rows': 3, 'maxlength': 800})}
        labels = {'duration_seconds': 'Clip length (seconds)'}

    def clean_youtube_url(self):
        url = self.cleaned_data.get('youtube_url')
        if url and not is_probable_youtube_url(url):
            raise forms.ValidationError('Enter a valid YouTube URL.')
        return url


# ======================================================================
# Admin-console forms (full CRUD over every tournament resource)
# ======================================================================
class SportForm(forms.ModelForm):
    class Meta:
        model = Sport
        fields = ['name', 'slug', 'icon', 'color_token', 'format_type', 'default_format',
                  'description']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class VenueForm(forms.ModelForm):
    class Meta:
        model = Venue
        fields = ['name', 'address', 'city', 'latitude', 'longitude']
        widgets = {'address': forms.Textarea(attrs={'rows': 2})}


class AdminTournamentForm(forms.ModelForm):
    """The admin can edit everything an organizer can, plus ownership, status and
    featuring — the organizer form deliberately exposes none of those."""

    class Meta:
        model = Tournament
        fields = ['name', 'slug', 'sport', 'organizer', 'format', 'draw_category', 'status',
                  'description', 'rules', 'venue', 'city', 'start_date', 'end_date',
                  'registration_deadline', 'banner_image', 'entry_fee', 'prize_pool',
                  'max_participants', 'youtube_url', 'is_featured', 'featured_order', 'is_removed']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'rules': forms.Textarea(attrs={'rows': 3}),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'registration_deadline': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].required = False
        self.fields['slug'].help_text = 'Leave blank to generate from the name.'
        self.fields['draw_category'].required = False

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_date'), cleaned.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'End date cannot be before start date.')
        return cleaned


class EventCategoryForm(forms.ModelForm):
    class Meta:
        model = EventCategory
        fields = ['tournament', 'name', 'distance_km', 'max_participants', 'entry_fee']


class AdminTeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ['name', 'sport', 'logo', 'captain']


class AdminFixtureForm(forms.ModelForm):
    class Meta:
        model = Fixture
        fields = ['tournament', 'event_category', 'round_name', 'round_no', 'sequence',
                  'session_no', 'scheduled_time', 'venue_detail', 'status', 'youtube_url',
                  'summary', 'result_published', 'is_removed']
        widgets = {
            'summary': forms.Textarea(attrs={'rows': 3}),
            'scheduled_time': forms.DateTimeInput(attrs={'type': 'datetime-local'},
                                                  format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['scheduled_time'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S',
                                                       '%Y-%m-%d %H:%M']


class AdminTeamEntryForm(forms.ModelForm):
    class Meta:
        model = TournamentTeamEntry
        fields = ['tournament', 'team', 'status', 'seed', 'group_name']


class AdminIndividualRegistrationForm(forms.ModelForm):
    class Meta:
        model = IndividualRegistration
        fields = ['tournament', 'event_category', 'player', 'display_name', 'bib_number',
                  'seed', 'status']


class AdminHighlightForm(forms.ModelForm):
    class Meta:
        model = Highlight
        fields = ['tournament', 'fixture', 'title', 'description', 'image', 'youtube_url',
                  'best_performer', 'duration_seconds', 'view_count', 'published_at',
                  'is_removed']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'published_at': forms.DateTimeInput(attrs={'type': 'datetime-local'},
                                                format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['published_at'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S',
                                                     '%Y-%m-%d %H:%M']

    def clean_youtube_url(self):
        url = self.cleaned_data.get('youtube_url')
        if url and not is_probable_youtube_url(url):
            raise forms.ValidationError('Enter a valid YouTube URL.')
        return url
