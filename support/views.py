from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.decorators import approved_organizer_required, player_required
from accounts.models import OrganizerProfile, PlayerProfile

from .emails import send_ticket_confirmation
from .forms import SupportTicketForm
from .models import ORGANIZER_SUBJECTS, PLAYER_SUBJECTS, ROLE_ORGANIZER, ROLE_PLAYER, SupportAttachment


def _submit_ticket(request, role, subjects, template, dashboard_url_name):
    initial = {
        'name': request.user.display_name,
        'phone_number': request.user.phone_number or '',
        'email': request.user.email,
    }
    if request.method == 'POST':
        form = SupportTicketForm(request.POST, subjects=subjects)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.user = request.user
            ticket.role = role
            ticket.save()
            for f in request.FILES.getlist('attachments'):
                SupportAttachment.objects.create(ticket=ticket, file=f)
            send_ticket_confirmation(ticket)
            messages.success(
                request, f'Your request has been submitted ({ticket.ref}). '
                          f'A confirmation email is on its way to {ticket.email}.')
            return redirect(dashboard_url_name)
    else:
        form = SupportTicketForm(initial=initial, subjects=subjects)
    return render(request, template, {'form': form, 'support_email': 'support@tournamentsc.com'})


@player_required
def player_support(request):
    PlayerProfile.objects.get_or_create(user=request.user)
    return _submit_ticket(request, ROLE_PLAYER, PLAYER_SUBJECTS,
                          'players/support.html', 'player_dashboard')


@approved_organizer_required
def organizer_support(request):
    OrganizerProfile.objects.get_or_create(user=request.user)
    return _submit_ticket(request, ROLE_ORGANIZER, ORGANIZER_SUBJECTS,
                          'organizer/support.html', 'organizer_dashboard')
