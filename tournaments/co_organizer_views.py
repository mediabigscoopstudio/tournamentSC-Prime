from datetime import timedelta
import hashlib
import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Notification
from .models import Tournament, TournamentCoOrganizer, TournamentCoOrganizerInvitation
from .tasks import _send


def _get_owned_by_creator(request, slug):
    """Only the original creator/owner of the tournament can manage co-organizers."""
    t = get_object_or_404(Tournament, slug=slug)
    if not request.user.is_authenticated:
        raise PermissionDenied('You must be signed in.')
    if t.organizer.user_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied('Only the tournament creator can manage co-organizers.')
    return t


@require_POST
def tournament_co_organizer_invite(request, slug):
    """Send an expiring email invitation to an existing TournamentSC user to co-organize."""
    t = _get_owned_by_creator(request, slug)
    username = (request.POST.get('username') or '').strip()

    if not username:
        messages.error(request, 'Please provide a username to invite.')
        return redirect('tournament_manage', slug=slug)

    User = get_user_model()
    invitee = User.objects.filter(username__iexact=username).first()
    if not invitee:
        messages.error(request, f"User '@{username}' was not found. Please verify their exact TournamentSC username.")
        return redirect('tournament_manage', slug=slug)

    if invitee.id == request.user.id:
        messages.error(request, 'You cannot invite yourself as a co-organizer.')
        return redirect('tournament_manage', slug=slug)

    # Check if already an active co-organizer
    if t.co_organizers.filter(user=invitee, is_active=True).exists():
        messages.info(request, f"@{invitee.username} is already an active co-organizer for this tournament.")
        return redirect('tournament_manage', slug=slug)

    # Invalidate any older pending invitations for this user on this tournament
    t.co_organizer_invitations.filter(user=invitee, status='PENDING').update(status='REVOKED')

    # Generate cryptographically secure token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
    expires_at = timezone.now() + timedelta(days=3)

    invitation = TournamentCoOrganizerInvitation.objects.create(
        tournament=t,
        invited_by=request.user,
        user=invitee,
        token_hash=token_hash,
        status='PENDING',
        expires_at=expires_at,
    )

    accept_url = request.build_absolute_uri(reverse('co_organizer_accept', kwargs={'token': raw_token}))

    # Dispatch email
    try:
        _send('co_organizer_invite', f'Invitation to Co-Organize {t.name}', invitee.email, {
            'tournament': t,
            'invited_by': request.user,
            'invitee': invitee,
            'accept_url': accept_url,
            'expires_at': expires_at,
        })
    except Exception as e:
        print(f"Warning: Co-organizer invitation email could not be sent: {e}")

    # Dispatch in-app notification
    Notification.push(
        invitee,
        f"{request.user.display_name} invited you to co-organize {t.name}.",
        url=accept_url,
        verb='invite'
    )

    messages.success(request, f"Invitation sent to @{invitee.username}. An email with the invitation link has been dispatched.")
    return redirect('tournament_manage', slug=slug)


@require_POST
def tournament_co_organizer_cancel_invite(request, slug, invite_id):
    """Revoke/cancel a pending invitation before it is accepted."""
    t = _get_owned_by_creator(request, slug)
    invitation = get_object_or_404(TournamentCoOrganizerInvitation, id=invite_id, tournament=t)
    invitation.status = 'REVOKED'
    invitation.save(update_fields=['status'])
    messages.success(request, f"Invitation for @{invitation.user.username} was cancelled.")
    return redirect('tournament_manage', slug=slug)


@require_POST
def tournament_co_organizer_revoke(request, slug, user_id):
    """Revoke an active co-organizer's access to the tournament."""
    t = _get_owned_by_creator(request, slug)
    co_org = get_object_or_404(TournamentCoOrganizer, tournament=t, user_id=user_id)
    co_org.is_active = False
    co_org.save(update_fields=['is_active'])

    Notification.push(
        co_org.user,
        f"Your co-organizer access to {t.name} was revoked by the tournament creator.",
        url=t.get_absolute_url(),
        verb='system'
    )

    messages.success(request, f"Co-organizer access for @{co_org.user.username} has been revoked.")
    return redirect('tournament_manage', slug=slug)


@login_required
def co_organizer_accept(request, token):
    """Accept an invitation to manage a specific tournament."""
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    invitation = TournamentCoOrganizerInvitation.objects.select_related(
        'tournament', 'invited_by', 'user'
    ).filter(token_hash=token_hash).first()

    if not invitation:
        messages.error(request, 'This invitation link is invalid or no longer exists.')
        return redirect('home')

    is_valid, err_msg = invitation.is_valid_for_acceptance(request.user)
    if not is_valid:
        messages.error(request, err_msg)
        return redirect(invitation.tournament.get_absolute_url())

    t = invitation.tournament

    # Mark accepted
    invitation.status = 'ACCEPTED'
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=['status', 'accepted_at'])

    # Activate co-organizer
    TournamentCoOrganizer.objects.update_or_create(
        tournament=t,
        user=request.user,
        defaults={'is_active': True, 'granted_by': invitation.invited_by}
    )

    # Notify creator
    Notification.push(
        invitation.invited_by,
        f"@{request.user.username} accepted your invitation to co-organize {t.name}.",
        url=reverse('tournament_manage', kwargs={'slug': t.slug}),
        verb='accept'
    )

    messages.success(request, f"You are now a co-organizer of {t.name}! You can manage fixtures, schedule matches, and record live scores.")
    return redirect('tournament_manage', slug=t.slug)

