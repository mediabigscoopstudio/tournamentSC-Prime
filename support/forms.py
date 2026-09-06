from django import forms

from .models import SupportTicket


class SupportTicketForm(forms.ModelForm):
    """The dashboard contact form. `subjects` narrows the dropdown to the
    role the ticket is being raised under (player vs organizer)."""

    class Meta:
        model = SupportTicket
        fields = ['name', 'phone_number', 'email', 'subject', 'message']
        widgets = {'message': forms.Textarea(attrs={'rows': 6})}

    def __init__(self, *args, subjects=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['subject'] = forms.ChoiceField(
            choices=[(s, s) for s in subjects], label='Subject')
