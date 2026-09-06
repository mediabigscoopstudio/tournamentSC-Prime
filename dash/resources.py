"""The admin console's resource registry.

Every section of the platform-admin panel (Sports, Tournaments, Teams, Fixtures,
Registrations, Highlights, News, Media, Users, Roles, …) is declared here as one
`Resource`. A single set of generic views in `dash/views.py` then renders the
list, the create/edit form, the delete confirmation and the row actions for all
of them — so adding a new managed resource is a declaration, not a new view,
a new URL and a new template.

A `Resource` says four things:
  * what model and form it manages,
  * what the list table looks like (columns / search / filters),
  * which lifecycle actions its rows support (approve, suspend, publish, …),
  * where it appears in the sidebar.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from django.contrib.auth.models import Group
from django.db.models import Q
from django.utils import timezone

from accounts.forms import RoleForm
from accounts.models import (AuditLog, Follow, Notification, OrganizerApplication,
                             OrganizerProfile, PlayerProfile, User)
from tournaments.forms import (AdminFixtureForm, AdminHighlightForm,
                               AdminIndividualRegistrationForm, AdminTeamEntryForm,
                               AdminTeamForm, AdminTournamentForm, EventCategoryForm,
                               SportForm, TeamMemberForm, VenueForm)
from support.emails import send_ticket_status_update
from support.models import SupportTicket
from tournaments.models import (EventCategory, Fixture, Highlight, IndividualRegistration,
                                Sport, Team, TeamMembership, Tournament, TournamentTeamEntry,
                                Venue)

from .forms import (AdminSupportTicketForm, AdminUserForm, AnnouncementForm, MediaAssetForm,
                    NewsForm, OrganizerProfileAdminForm, PlayerProfileAdminForm)
from .models import Announcement, MediaAsset, News


# ======================================================================
# Declarations
# ======================================================================
@dataclass(frozen=True)
class Column:
    label: str
    value: Callable[[Any], Any]
    kind: str = 'text'          # text | badge | bool | link


@dataclass(frozen=True)
class Action:
    """A row-level POST action, e.g. 'approve' or 'suspend'."""
    name: str
    label: str
    style: str = 'ghost'                        # primary | secondary | ghost | danger
    # Only offer the action when this predicate says the row is eligible.
    visible: Callable[[Any], bool] = lambda obj: True
    confirm: str = ''


@dataclass
class Resource:
    key: str
    label: str
    model: Any
    form: Any
    group: str
    icon: str = '•'
    singular: str = ''
    columns: list = field(default_factory=list)
    search_fields: list = field(default_factory=list)
    filters: list = field(default_factory=list)   # [(param, label, [(value, label)...], orm_field)]
    ordering: tuple = ('-id',)
    select_related: tuple = ()
    prefetch_related: tuple = ()
    actions: list = field(default_factory=list)
    can_create: bool = True
    can_edit: bool = True
    can_delete: bool = True
    superuser_only: bool = False
    # Applies a row action. Returns a human message. Raises ValueError to refuse.
    apply_action: Optional[Callable] = None
    help_text: str = ''

    def __post_init__(self):
        if not self.singular:
            self.singular = self.label.rstrip('s') or self.label

    def queryset(self):
        qs = self.model._default_manager.all()
        if self.select_related:
            qs = qs.select_related(*self.select_related)
        if self.prefetch_related:
            qs = qs.prefetch_related(*self.prefetch_related)
        return qs.order_by(*self.ordering)

    def search(self, qs, term):
        if not term or not self.search_fields:
            return qs
        q = Q()
        for f in self.search_fields:
            q |= Q(**{f'{f}__icontains': term})
        return qs.filter(q)


# ======================================================================
# Small helpers used by the column definitions
# ======================================================================
def _dt(value):
    return timezone.localtime(value).strftime('%d %b %Y, %H:%M') if value else '—'


def _date(value):
    return value.strftime('%d %b %Y') if value else '—'


def _yes(value):
    return 'Yes' if value else 'No'


def _audit(request, action, target, detail=''):
    AuditLog.record(request.user, action, target, detail)


# ======================================================================
# Action handlers  (one per resource that has lifecycle actions)
# ======================================================================
def _act_tournament(request, t, action):
    if action == 'publish':
        if t.status != 'DRAFT':
            raise ValueError(f'"{t.name}" is not a draft.')
        t.status = 'PUBLISHED'
        t.save(update_fields=['status', 'updated_at'])
        Notification.push(t.organizer.user, f'An admin published "{t.name}".',
                          url=t.get_absolute_url(), verb='publish')
        _audit(request, 'publish_tournament', t.name)
        return f'Published "{t.name}".'
    if action == 'unpublish':
        t.status = 'DRAFT'
        t.save(update_fields=['status', 'updated_at'])
        _audit(request, 'unpublish_tournament', t.name)
        return f'"{t.name}" is back to draft.'
    if action == 'feature':
        t.is_featured = not t.is_featured
        if t.is_featured and not t.featured_order:
            t.featured_order = Tournament.objects.filter(is_featured=True).count() + 1
        t.save(update_fields=['is_featured', 'featured_order', 'updated_at'])
        _audit(request, 'feature_tournament' if t.is_featured else 'unfeature_tournament', t.name)
        return f'{"Featured" if t.is_featured else "Unfeatured"} "{t.name}".'
    if action == 'archive':
        t.is_removed = True
        t.save(update_fields=['is_removed', 'updated_at'])
        Notification.push(t.organizer.user,
                          f'"{t.name}" was removed from public view by an administrator.',
                          verb='moderation')
        _audit(request, 'archive_tournament', t.name)
        return f'Archived "{t.name}" — it is no longer public.'
    if action == 'restore':
        t.is_removed = False
        t.save(update_fields=['is_removed', 'updated_at'])
        _audit(request, 'restore_tournament', t.name)
        return f'Restored "{t.name}".'
    if action == 'cancel':
        t.status = 'CANCELLED'
        t.save(update_fields=['status', 'updated_at'])
        _audit(request, 'cancel_tournament', t.name)
        return f'Cancelled "{t.name}".'
    raise ValueError('Unknown action.')


def _act_user(request, u, action):
    if u.is_superuser and action in ('suspend', 'deactivate'):
        raise ValueError('A superuser cannot be suspended or deactivated.')
    if u.id == request.user.id and action in ('suspend', 'deactivate'):
        raise ValueError('You cannot lock yourself out.')

    if action == 'suspend':
        u.is_suspended = True
        u.suspended_reason = request.POST.get('reason', '')
        u.save(update_fields=['is_suspended', 'suspended_reason'])
        _audit(request, 'suspend_user', u.email, u.suspended_reason)
        return f'Suspended {u.email}.'
    if action == 'activate':
        u.is_suspended = False
        u.suspended_reason = ''
        u.is_active = True
        u.save(update_fields=['is_suspended', 'suspended_reason', 'is_active'])
        _audit(request, 'activate_user', u.email)
        return f'Reinstated {u.email}.'
    if action == 'verify':
        u.is_verified = not u.is_verified
        u.save(update_fields=['is_verified'])
        _audit(request, 'toggle_verify', u.email)
        return f'{"Verified" if u.is_verified else "Unverified"} {u.email}.'
    raise ValueError('Unknown action.')


def _act_organizer(request, profile, action):
    if action == 'approve':
        profile.is_approved = True
        profile.approved_by = request.user
        profile.approved_at = timezone.now()
        profile.rejection_reason = ''
        profile.save()
        OrganizerApplication.objects.filter(
            user=profile.user, status='PENDING').update(
            status='APPROVED', reviewed_by=request.user, reviewed_at=timezone.now())
        Notification.push(profile.user,
                          'Your organizer account was approved. You can now create tournaments.',
                          url='/organizer/', verb='approval')
        _audit(request, 'approve_organizer', profile.user.email)
        return f'Approved {profile.user.display_name} as an organizer.'
    if action == 'revoke':
        profile.is_approved = False
        profile.save(update_fields=['is_approved', 'updated_at'])
        Notification.push(profile.user, 'Your organizer permissions were revoked.',
                          url='/organizer/status', verb='approval')
        _audit(request, 'revoke_organizer', profile.user.email)
        return f'Revoked organizer access for {profile.user.display_name}.'
    raise ValueError('Unknown action.')


def _act_application(request, app, action):
    profile, _ = OrganizerProfile.objects.get_or_create(user=app.user)
    app.reviewed_by = request.user
    app.reviewed_at = timezone.now()
    if action == 'approve':
        app.status = 'APPROVED'
        profile.is_approved = True
        profile.approved_by = request.user
        profile.approved_at = timezone.now()
        profile.rejection_reason = ''
        profile.save()
        app.save()
        Notification.push(app.user,
                          'Your organizer application was approved! You can now create tournaments.',
                          url='/organizer/', verb='approval')
        _audit(request, 'approve_organizer', app.user.email)
        return f'Approved {app.user.display_name} as an organizer.'
    if action == 'reject':
        reason = (request.POST.get('reason') or '').strip()
        app.status = 'REJECTED'
        app.rejection_reason = reason
        profile.is_approved = False
        profile.rejection_reason = reason
        profile.save()
        app.save()
        Notification.push(app.user,
                          f'Your organizer application was not approved. {reason}'.strip(),
                          url='/organizer/status', verb='approval')
        _audit(request, 'reject_organizer', app.user.email, reason)
        return 'Application rejected.'
    raise ValueError('Unknown action.')


def _act_support_ticket(request, ticket, action):
    if action != 'close':
        raise ValueError('Unknown action.')
    if ticket.status == SupportTicket.STATUS_CLOSED:
        raise ValueError('This ticket is already closed.')
    ticket.status = SupportTicket.STATUS_CLOSED
    ticket.closed_at = timezone.now()
    ticket.resolution_note = (request.POST.get('reason') or '').strip()
    ticket.save(update_fields=['status', 'closed_at', 'resolution_note', 'updated_at'])
    send_ticket_status_update(ticket)
    _audit(request, 'close_support_ticket', ticket.ref, ticket.resolution_note)
    return f'Closed {ticket.ref} and emailed {ticket.email}.'


def _act_entry_status(request, entry, action):
    """Shared by team entries and individual registrations."""
    if action not in ('approve', 'reject'):
        raise ValueError('Unknown action.')
    entry.status = 'APPROVED' if action == 'approve' else 'REJECTED'
    entry.save(update_fields=['status'])
    user = getattr(getattr(entry, 'player', None), 'user', None)
    if user:
        Notification.push(user,
                          f'Your entry to {entry.tournament.name} was '
                          f'{"accepted" if action == "approve" else "declined"}.',
                          url=entry.tournament.get_absolute_url(), verb='registration')
    _audit(request, f'{action}_entry', str(entry))
    return f'Entry {action}d.'


def _act_membership(request, m, action):
    if action == 'approve':
        m.is_approved = True
        m.save(update_fields=['is_approved'])
        _audit(request, 'approve_membership', str(m))
        return 'Membership approved.'
    if action == 'reject':
        m.is_approved = False
        m.save(update_fields=['is_approved'])
        _audit(request, 'reject_membership', str(m))
        return 'Membership set to pending.'
    raise ValueError('Unknown action.')


def _act_publishable(request, obj, action):
    """News / Announcements: publish, unpublish, archive, restore."""
    label = getattr(obj, 'title', str(obj))
    if action == 'publish':
        obj.publish()
        _audit(request, f'publish_{obj._meta.model_name}', label)
        return f'Published "{label}".'
    if action == 'unpublish':
        obj.unpublish()
        _audit(request, f'unpublish_{obj._meta.model_name}', label)
        return f'"{label}" is back to draft.'
    if action == 'archive':
        obj.archive()
        _audit(request, f'archive_{obj._meta.model_name}', label)
        return f'Archived "{label}".'
    if action == 'restore':
        obj.restore()
        _audit(request, f'restore_{obj._meta.model_name}', label)
        return f'Restored "{label}".'
    raise ValueError('Unknown action.')


def _act_highlight(request, h, action):
    if action == 'publish':
        h.published_at = h.published_at or timezone.now()
        h.is_removed = False
        h.save(update_fields=['published_at', 'is_removed', 'updated_at'])
        _audit(request, 'publish_highlight', h.title)
        return f'Published "{h.title}".'
    if action == 'unpublish':
        h.published_at = None
        h.save(update_fields=['published_at', 'updated_at'])
        _audit(request, 'unpublish_highlight', h.title)
        return f'"{h.title}" is back to draft.'
    if action == 'archive':
        h.is_removed = True
        h.save(update_fields=['is_removed', 'updated_at'])
        _audit(request, 'archive_highlight', h.title)
        return f'Archived "{h.title}".'
    if action == 'restore':
        h.is_removed = False
        h.save(update_fields=['is_removed', 'updated_at'])
        _audit(request, 'restore_highlight', h.title)
        return f'Restored "{h.title}".'
    raise ValueError('Unknown action.')


def _act_media(request, m, action):
    if action == 'archive':
        m.is_archived = True
        m.save(update_fields=['is_archived', 'updated_at'])
        _audit(request, 'archive_media', m.title)
        return f'Archived "{m.title}".'
    if action == 'restore':
        m.is_archived = False
        m.save(update_fields=['is_archived', 'updated_at'])
        _audit(request, 'restore_media', m.title)
        return f'Restored "{m.title}".'
    raise ValueError('Unknown action.')


def _act_fixture(request, fx, action):
    if action == 'archive':
        fx.is_removed = True
        fx.save(update_fields=['is_removed', 'updated_at'])
        _audit(request, 'archive_fixture', str(fx))
        return 'Fixture removed from public view.'
    if action == 'restore':
        fx.is_removed = False
        fx.save(update_fields=['is_removed', 'updated_at'])
        _audit(request, 'restore_fixture', str(fx))
        return 'Fixture restored.'
    raise ValueError('Unknown action.')


# ======================================================================
# The registry
# ======================================================================
STATUS_FILTER = ('status', 'Status', [
    ('DRAFT', 'Draft'), ('PUBLISHED', 'Upcoming'), ('ONGOING', 'Live'),
    ('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled')], 'status')

RESOURCES = {}


def register(resource):
    RESOURCES[resource.key] = resource
    return resource


# ---- Catalogue -------------------------------------------------------
register(Resource(
    key='sports', label='Sports', singular='Sport', model=Sport, form=SportForm,
    group='Catalogue', icon='🎽', ordering=('name',),
    search_fields=['name', 'slug'],
    columns=[
        Column('Sport', lambda o: f'{o.icon} {o.name}'),
        Column('Slug', lambda o: o.slug),
        Column('Type', lambda o: o.format_type, kind='badge'),
        Column('Default format', lambda o: o.get_default_format_display()),
        Column('Tournaments', lambda o: o.tournaments.count()),
    ],
    help_text='The seven sports the platform ships with. A sport maps to a format engine.',
))

register(Resource(
    key='venues', label='Venues', singular='Venue', model=Venue, form=VenueForm,
    group='Catalogue', icon='📍', ordering=('name',),
    search_fields=['name', 'city'],
    columns=[
        Column('Venue', lambda o: o.name),
        Column('City', lambda o: o.city),
        Column('Address', lambda o: (o.address or '—')[:60]),
    ],
))

register(Resource(
    key='categories', label='Tournament categories', singular='Category',
    model=EventCategory, form=EventCategoryForm,
    group='Catalogue', icon='🗂️', ordering=('tournament__name', 'name'),
    select_related=('tournament',), search_fields=['name', 'tournament__name'],
    columns=[
        Column('Category', lambda o: o.name),
        Column('Tournament', lambda o: o.tournament.name),
        Column('Distance', lambda o: f'{o.distance_km} km' if o.distance_km else '—'),
        Column('Max entrants', lambda o: o.max_participants or '—'),
        Column('Entry fee', lambda o: o.entry_fee if o.entry_fee is not None else '—'),
    ],
    help_text='Sub-brackets inside one tournament: 5K/10K, weight classes, rating bands. '
              'A distance turns on the pace column for that leaderboard.',
))

# ---- Competition -----------------------------------------------------
register(Resource(
    key='tournaments', label='Tournaments', singular='Tournament',
    model=Tournament, form=AdminTournamentForm,
    group='Competition', icon='🏆', ordering=('-is_featured', 'featured_order', '-created_at'),
    select_related=('sport', 'organizer__user'),
    search_fields=['name', 'city', 'organizer__user__email'],
    filters=[STATUS_FILTER,
             ('featured', 'Featured', [('1', 'Featured only')], 'is_featured'),
             ('archived', 'Archived', [('1', 'Archived only')], 'is_removed')],
    columns=[
        Column('Tournament', lambda o: o.name),
        Column('Sport', lambda o: o.sport.name),
        Column('Organizer', lambda o: o.organizer.user.display_name),
        Column('Status', lambda o: o.get_status_display(), kind='badge'),
        Column('Starts', lambda o: _date(o.start_date)),
        Column('Venue', lambda o: o.venue.name if o.venue else (o.city or '—')),
        Column('Prize pool', lambda o: o.prize_pool if o.prize_pool is not None else '—'),
        Column('Entrants', lambda o: o.participant_count()),
        Column('Followers', lambda o: o.followers.count()),
        Column('Featured', lambda o: o.is_featured, kind='bool'),
        Column('Archived', lambda o: o.is_removed, kind='bool'),
    ],
    actions=[
        Action('publish', 'Publish', 'primary', visible=lambda o: o.status == 'DRAFT'),
        Action('unpublish', 'Unpublish', 'ghost', visible=lambda o: o.status == 'PUBLISHED'),
        Action('feature', 'Feature', 'secondary', visible=lambda o: not o.is_featured),
        Action('feature', 'Unfeature', 'ghost', visible=lambda o: o.is_featured),
        Action('cancel', 'Cancel', 'ghost',
               visible=lambda o: o.status not in ('CANCELLED', 'COMPLETED'),
               confirm='Cancel this tournament?'),
        Action('archive', 'Archive', 'danger', visible=lambda o: not o.is_removed,
               confirm='Hide this tournament from the public site?'),
        Action('restore', 'Restore', 'primary', visible=lambda o: o.is_removed),
    ],
    apply_action=_act_tournament,
))

register(Resource(
    key='fixtures', label='Matches & fixtures', singular='Fixture',
    model=Fixture, form=AdminFixtureForm,
    group='Competition', icon='⚔️', ordering=('-id',),
    select_related=('tournament__sport',), prefetch_related=('participants__team',
                                                             'participants__player__user'),
    search_fields=['tournament__name', 'round_name'],
    filters=[('status', 'Status', [('SCHEDULED', 'Scheduled'), ('LIVE', 'Live'),
                                   ('COMPLETED', 'Completed'), ('POSTPONED', 'Postponed'),
                                   ('CANCELLED', 'Cancelled')], 'status')],
    columns=[
        Column('Match', lambda o: ' vs '.join(p.name for p in o.participants.all()[:4]) or '—'),
        Column('Tournament', lambda o: o.tournament.name),
        Column('Round', lambda o: o.round_name or f'Round {o.round_no}'),
        Column('Kick-off', lambda o: _dt(o.scheduled_time)),
        Column('Status', lambda o: o.get_status_display(), kind='badge'),
        Column('Archived', lambda o: o.is_removed, kind='bool'),
    ],
    actions=[
        Action('archive', 'Archive', 'danger', visible=lambda o: not o.is_removed),
        Action('restore', 'Restore', 'primary', visible=lambda o: o.is_removed),
    ],
    apply_action=_act_fixture,
))

register(Resource(
    key='teams', label='Teams', singular='Team', model=Team, form=AdminTeamForm,
    group='Competition', icon='🛡️', ordering=('name',),
    select_related=('sport', 'captain__user'), search_fields=['name'],
    columns=[
        Column('Team', lambda o: o.name),
        Column('Sport', lambda o: o.sport.name),
        Column('Captain', lambda o: o.captain.user.display_name if o.captain else '—'),
        Column('Roster', lambda o: o.memberships.count()),
        Column('Tournaments', lambda o: o.entries.count()),
    ],
))

register(Resource(
    key='memberships', label='Team members', singular='Team member',
    model=TeamMembership, form=TeamMemberForm,
    group='Competition', icon='👕', ordering=('team__name', 'id'),
    select_related=('team', 'player__user'), search_fields=['team__name', 'display_name'],
    filters=[('pending', 'Approval', [('0', 'Pending only')], 'is_approved')],
    columns=[
        Column('Member', lambda o: o.name),
        Column('Team', lambda o: o.team.name),
        Column('Role', lambda o: o.get_role_display(), kind='badge'),
        Column('Jersey', lambda o: o.jersey_number or '—'),
        Column('Approved', lambda o: o.is_approved, kind='bool'),
    ],
    actions=[
        Action('approve', 'Approve', 'primary', visible=lambda o: not o.is_approved),
        Action('reject', 'Set pending', 'ghost', visible=lambda o: o.is_approved),
    ],
    apply_action=_act_membership,
))

register(Resource(
    key='team-entries', label='Team registrations', singular='Team registration',
    model=TournamentTeamEntry, form=AdminTeamEntryForm,
    group='Competition', icon='📋', ordering=('-registered_at',),
    select_related=('tournament', 'team'), search_fields=['team__name', 'tournament__name'],
    filters=[('status', 'Status', [('PENDING', 'Pending'), ('APPROVED', 'Approved'),
                                   ('REJECTED', 'Rejected')], 'status')],
    columns=[
        Column('Team', lambda o: o.team.name),
        Column('Tournament', lambda o: o.tournament.name),
        Column('Seed', lambda o: o.seed or '—'),
        Column('Status', lambda o: o.get_status_display(), kind='badge'),
        Column('Registered', lambda o: _dt(o.registered_at)),
    ],
    actions=[
        Action('approve', 'Approve', 'primary', visible=lambda o: o.status != 'APPROVED'),
        Action('reject', 'Reject', 'danger', visible=lambda o: o.status != 'REJECTED'),
    ],
    apply_action=_act_entry_status,
))

register(Resource(
    key='registrations', label='Player registrations', singular='Registration',
    model=IndividualRegistration, form=AdminIndividualRegistrationForm,
    group='Competition', icon='✍️', ordering=('-registered_at',),
    select_related=('tournament', 'player__user'),
    search_fields=['display_name', 'tournament__name', 'player__user__email'],
    filters=[('status', 'Status', [('PENDING', 'Pending'), ('APPROVED', 'Approved'),
                                   ('REJECTED', 'Rejected')], 'status')],
    columns=[
        Column('Entrant', lambda o: o.name),
        Column('Tournament', lambda o: o.tournament.name),
        Column('Bib', lambda o: o.bib_number or '—'),
        Column('Status', lambda o: o.get_status_display(), kind='badge'),
        Column('Registered', lambda o: _dt(o.registered_at)),
    ],
    actions=[
        Action('approve', 'Approve', 'primary', visible=lambda o: o.status != 'APPROVED'),
        Action('reject', 'Reject', 'danger', visible=lambda o: o.status != 'REJECTED'),
    ],
    apply_action=_act_entry_status,
))

# ---- People ----------------------------------------------------------
register(Resource(
    key='users', label='Users', singular='User', model=User, form=AdminUserForm,
    group='People', icon='👥', ordering=('-date_joined',),
    search_fields=['email', 'first_name', 'username', 'phone_number'],
    filters=[('role', 'Role', [('staff', 'Administrators'), ('organizer', 'Organizers'),
                               ('player', 'Players')], None),
             ('suspended', 'State', [('1', 'Suspended only')], 'is_suspended')],
    columns=[
        Column('Name', lambda o: o.display_name),
        Column('Email', lambda o: o.email),
        Column('Role', lambda o: ('Admin' if o.is_staff else
                                  'Organizer' if o.has_organizer_profile else 'Player'),
               kind='badge'),
        Column('Verified', lambda o: o.is_verified, kind='bool'),
        Column('Suspended', lambda o: o.is_suspended, kind='bool'),
        Column('Joined', lambda o: _date(o.date_joined)),
    ],
    actions=[
        Action('suspend', 'Suspend', 'danger',
               visible=lambda o: not o.is_suspended and not o.is_superuser,
               confirm='Suspend this account? They will be logged out immediately.'),
        Action('activate', 'Activate', 'primary', visible=lambda o: o.is_suspended),
        Action('verify', 'Toggle verify', 'ghost'),
    ],
    apply_action=_act_user,
    can_delete=True,
    help_text='Every account on the platform. Administrators are created here by ticking '
              '"platform administrator" — never through the public sign-up.',
))

register(Resource(
    key='organizers', label='Organizers', singular='Organizer',
    model=OrganizerProfile, form=OrganizerProfileAdminForm,
    group='People', icon='🎪', ordering=('-created_at',),
    select_related=('user', 'approved_by'),
    search_fields=['user__email', 'user__first_name', 'organization_name'],
    filters=[('approved', 'Approval', [('1', 'Approved'), ('0', 'Not approved')], 'is_approved')],
    columns=[
        Column('Organizer', lambda o: o.user.display_name),
        Column('Email', lambda o: o.user.email),
        Column('Organisation', lambda o: o.organization_name or '—'),
        Column('Approved', lambda o: o.is_approved, kind='bool'),
        Column('Tournaments', lambda o: o.tournaments.count()),
        Column('Approved on', lambda o: _date(o.approved_at)),
    ],
    actions=[
        Action('approve', 'Approve', 'primary', visible=lambda o: not o.is_approved),
        Action('revoke', 'Revoke', 'danger', visible=lambda o: o.is_approved,
               confirm='Revoke organizer permissions? Their tournaments stay online.'),
    ],
    apply_action=_act_organizer,
    can_create=False,
))

register(Resource(
    key='applications', label='Organizer applications', singular='Application',
    model=OrganizerApplication, form=None,
    group='People', icon='📝', ordering=('-created_at',),
    select_related=('user', 'reviewed_by'), prefetch_related=('sports',),
    search_fields=['user__email', 'affiliation'],
    filters=[('status', 'Status', [('PENDING', 'Pending'), ('APPROVED', 'Approved'),
                                   ('REJECTED', 'Rejected')], 'status')],
    columns=[
        Column('Applicant', lambda o: o.user.display_name),
        Column('Email', lambda o: o.user.email),
        Column('Affiliation', lambda o: o.affiliation),
        Column('Sports', lambda o: ', '.join(s.name for s in o.sports.all()) or '—'),
        Column('Status', lambda o: o.get_status_display(), kind='badge'),
        Column('Applied', lambda o: _dt(o.created_at)),
    ],
    actions=[
        Action('approve', 'Approve', 'primary', visible=lambda o: o.status == 'PENDING'),
        Action('reject', 'Reject', 'danger', visible=lambda o: o.status == 'PENDING'),
    ],
    apply_action=_act_application,
    can_create=False, can_edit=False,
))

register(Resource(
    key='players', label='Players', singular='Player',
    model=PlayerProfile, form=PlayerProfileAdminForm,
    group='People', icon='🏃', ordering=('-created_at',),
    select_related=('user',), prefetch_related=('sports',),
    search_fields=['user__email', 'user__first_name', 'city'],
    columns=[
        Column('Player', lambda o: o.user.display_name),
        Column('Email', lambda o: o.user.email),
        Column('City', lambda o: o.city or '—'),
        Column('Rating', lambda o: o.rating or '—'),
        Column('Sports', lambda o: ', '.join(s.name for s in o.sports.all()) or '—'),
        Column('Suspended', lambda o: o.user.is_suspended, kind='bool'),
    ],
    can_create=False,
))

register(Resource(
    key='follows', label='Followers', singular='Follow', model=Follow, form=None,
    group='People', icon='🔔', ordering=('-created_at',),
    select_related=('user', 'tournament__sport'),
    search_fields=['user__email', 'tournament__name'],
    columns=[
        Column('Follower', lambda o: o.user.display_name),
        Column('Email', lambda o: o.user.email),
        Column('Tournament', lambda o: o.tournament.name),
        Column('Sport', lambda o: o.tournament.sport.name),
        Column('Since', lambda o: _dt(o.created_at)),
    ],
    can_create=False, can_edit=False,
    help_text='Who is following what. Followers are notified whenever a result is posted.',
))

# ---- Support -----------------------------------------------------------
register(Resource(
    key='support', label='Support tickets', singular='Support ticket',
    model=SupportTicket, form=AdminSupportTicketForm,
    group='Support', icon='🎧', ordering=('-created_at',),
    select_related=('user',), search_fields=['name', 'email', 'subject'],
    filters=[('status', 'Status', [('OPEN', 'Open'), ('CLOSED', 'Closed')], 'status'),
             ('role', 'Raised by', [('PLAYER', 'Player'), ('ORGANIZER', 'Organizer')], 'role')],
    columns=[
        Column('Ref', lambda o: o.ref),
        Column('From', lambda o: f'{o.name} ({o.email})'),
        Column('Role', lambda o: o.get_role_display(), kind='badge'),
        Column('Subject', lambda o: o.subject),
        Column('Status', lambda o: o.get_status_display(), kind='badge'),
        Column('Raised', lambda o: _dt(o.created_at)),
        Column('Closed', lambda o: _dt(o.closed_at)),
    ],
    actions=[
        Action('close', 'Close & notify', 'primary', visible=lambda o: o.status == 'OPEN',
               confirm='Close this ticket and email the requester?'),
    ],
    apply_action=_act_support_ticket,
    can_create=False,
    help_text='Contact-form requests from player and organizer dashboards. Closing a ticket '
              'emails the requester at the address they submitted.',
))

# ---- Content ---------------------------------------------------------
register(Resource(
    key='news', label='News', singular='Article', model=News, form=NewsForm,
    group='Content', icon='📰', ordering=('-created_at',),
    select_related=('sport', 'tournament'), search_fields=['title', 'summary'],
    filters=[('status', 'Status', [('DRAFT', 'Draft'), ('PUBLISHED', 'Published')], 'status'),
             ('archived', 'Archived', [('1', 'Archived only')], 'is_archived')],
    columns=[
        Column('Title', lambda o: o.title),
        Column('Sport', lambda o: o.sport.name if o.sport else '—'),
        Column('Status', lambda o: o.get_status_display(), kind='badge'),
        Column('Published', lambda o: _dt(o.published_at)),
        Column('Archived', lambda o: o.is_archived, kind='bool'),
    ],
    actions=[
        Action('publish', 'Publish', 'primary', visible=lambda o: not o.is_live),
        Action('unpublish', 'Unpublish', 'ghost', visible=lambda o: o.is_live),
        Action('archive', 'Archive', 'danger', visible=lambda o: not o.is_archived),
        Action('restore', 'Restore', 'primary', visible=lambda o: o.is_archived),
    ],
    apply_action=_act_publishable,
))

register(Resource(
    key='announcements', label='Announcements', singular='Announcement',
    model=Announcement, form=AnnouncementForm,
    group='Content', icon='📣', ordering=('-created_at',),
    search_fields=['title', 'message'],
    filters=[('status', 'Status', [('DRAFT', 'Draft'), ('PUBLISHED', 'Published')], 'status')],
    columns=[
        Column('Title', lambda o: o.title),
        Column('Level', lambda o: o.get_level_display(), kind='badge'),
        Column('Status', lambda o: o.get_status_display(), kind='badge'),
        Column('On site now', lambda o: o.is_current, kind='bool'),
        Column('Ends', lambda o: _dt(o.ends_at)),
    ],
    actions=[
        Action('publish', 'Publish', 'primary', visible=lambda o: not o.is_live),
        Action('unpublish', 'Unpublish', 'ghost', visible=lambda o: o.is_live),
        Action('archive', 'Archive', 'danger', visible=lambda o: not o.is_archived),
        Action('restore', 'Restore', 'primary', visible=lambda o: o.is_archived),
    ],
    apply_action=_act_publishable,
    help_text='A published announcement shows as a banner across the public site.',
))

register(Resource(
    key='highlights', label='Highlights', singular='Highlight',
    model=Highlight, form=AdminHighlightForm,
    group='Content', icon='🎬', ordering=('-created_at',),
    select_related=('tournament', 'fixture'), search_fields=['title', 'tournament__name'],
    filters=[('archived', 'Archived', [('1', 'Archived only')], 'is_removed')],
    columns=[
        Column('Title', lambda o: o.title),
        Column('Tournament', lambda o: o.tournament.name),
        Column('Length', lambda o: o.duration_display or '—'),
        Column('Views', lambda o: o.view_count),
        Column('Published', lambda o: _dt(o.published_at)),
        Column('Archived', lambda o: o.is_removed, kind='bool'),
    ],
    actions=[
        Action('publish', 'Publish', 'primary', visible=lambda o: not o.is_published),
        Action('unpublish', 'Unpublish', 'ghost', visible=lambda o: o.is_published),
        Action('archive', 'Archive', 'danger', visible=lambda o: not o.is_removed),
        Action('restore', 'Restore', 'primary', visible=lambda o: o.is_removed),
    ],
    apply_action=_act_highlight,
))

register(Resource(
    key='media', label='Media library', singular='Asset',
    model=MediaAsset, form=MediaAssetForm,
    group='Content', icon='🖼️', ordering=('-created_at',),
    select_related=('tournament', 'uploaded_by'), search_fields=['title', 'alt_text'],
    filters=[('kind', 'Kind', [('image', 'Image'), ('video', 'Video link'),
                               ('document', 'Document')], 'kind'),
             ('archived', 'Archived', [('1', 'Archived only')], 'is_archived')],
    columns=[
        Column('Title', lambda o: o.title),
        Column('Kind', lambda o: o.get_kind_display(), kind='badge'),
        Column('File', lambda o: o.url or '—', kind='link'),
        Column('Uploaded', lambda o: _dt(o.created_at)),
        Column('Archived', lambda o: o.is_archived, kind='bool'),
    ],
    actions=[
        Action('archive', 'Archive', 'danger', visible=lambda o: not o.is_archived),
        Action('restore', 'Restore', 'primary', visible=lambda o: o.is_archived),
    ],
    apply_action=_act_media,
))

# ---- System ----------------------------------------------------------
register(Resource(
    key='roles', label='Roles', singular='Role', model=Group, form=RoleForm,
    group='System', icon='🔑', ordering=('name',), search_fields=['name'],
    prefetch_related=('permissions',),
    columns=[
        Column('Role', lambda o: o.name),
        Column('Permissions', lambda o: o.permissions.count()),
        Column('Members', lambda o: o.user_set.count()),
    ],
    superuser_only=True,
    help_text='A role is a group of permissions you can grant to staff accounts.',
))
