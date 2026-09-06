import csv

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import approved_organizer_required, player_required
from accounts.models import Follow, Notification, PlayerProfile
from . import constants as C
from .forms import (FixtureScheduleForm, HighlightForm, IndividualEntryForm, TeamForm,
                    TournamentForm)
from .models import (Fixture, FixtureParticipant, IndividualRegistration, ScoreEvent, Team,
                     TeamMembership, Tournament, TournamentTeamEntry)
from .services import apply_result, sync_tournament_status
from .utils import format_ms, parse_time_to_ms


# ======================================================================
# Ownership guard
# ======================================================================
def _owned(request, slug):
    """An organizer may only manage tournaments they own.

    Platform admins do NOT get a back door here — they manage every tournament
    from the admin console. Keeping this check strict is what makes the two
    applications genuinely separate.
    """
    t = get_object_or_404(Tournament, slug=slug)
    if t.organizer.user_id != request.user.id:
        raise PermissionDenied('You do not manage this tournament.')
    return t


def _is_pair_tournament(t):
    """True for a racket-sport Doubles/Mixed Doubles tournament, where every
    "team" is really a 2-player pair — caps roster size at 2 (see
    team_member_add/team_members_bulk_import)."""
    return t.sport.slug in C.RACKET_SPORTS and t.draw_category in C.DRAW_TEAM_CATEGORIES


# ======================================================================
# Organizer dashboard
# ======================================================================
@approved_organizer_required
def organizer_dashboard(request):
    tournaments = request.user.organizer_profile.tournaments.select_related('sport').all()
    pending_members = TeamMembership.objects.filter(
        team__entries__tournament__organizer=request.user.organizer_profile,
        is_approved=False).count()
    pending_entries = TournamentTeamEntry.objects.filter(
        tournament__organizer=request.user.organizer_profile, status='PENDING').count()
    return render(request, 'organizer/dashboard.html', {
        'tournaments': tournaments,
        'counts': {
            'total': tournaments.count(),
            'live': tournaments.filter(status='ONGOING').count(),
            'draft': tournaments.filter(status='DRAFT').count(),
            'pending_requests': pending_members + pending_entries,
        },
    })


@approved_organizer_required
def tournament_create(request):
    form = TournamentForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        t = form.save(commit=False)
        t.organizer = request.user.organizer_profile
        t.save()
        messages.success(request, 'Tournament created as a draft. Add participants next.')
        return redirect('tournament_manage', slug=t.slug)
    return render(request, 'organizer/tournament_form.html', {'form': form, 'create': True})


@approved_organizer_required
def tournament_edit(request, slug):
    t = _owned(request, slug)
    form = TournamentForm(request.POST or None, request.FILES or None, instance=t,
                          locked=t.fixtures_generated)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Tournament updated.')
        return redirect('tournament_manage', slug=t.slug)
    return render(request, 'organizer/tournament_form.html',
                  {'form': form, 'create': False, 'tournament': t})


@approved_organizer_required
def tournament_manage(request, slug):
    t = _owned(request, slug)
    return render(request, 'organizer/manage.html', {
        'tournament': t,
        'participant_count': t.participant_count(),
        'fixtures': t.fixtures.filter(is_removed=False).select_related('event_category'),
        'manual_mode': _manual_fixtures_supported(t),
        'pool_mode': t.is_pool_stage,
        'pending_team_members': TeamMembership.objects.filter(
            team__entries__tournament=t, is_approved=False).select_related('team', 'player__user'),
    })


@approved_organizer_required
@require_POST
def tournament_publish(request, slug):
    """Take a draft live. Publishing is the organizer's own switch — the admin
    can still unpublish or archive it from the console."""
    t = _owned(request, slug)
    if t.status != 'DRAFT':
        messages.info(request, f'"{t.name}" is already published.')
    elif t.participant_count() < 2:
        messages.error(request, 'Add at least two participants before publishing.')
    else:
        t.status = 'PUBLISHED'
        t.save(update_fields=['status', 'updated_at'])
        messages.success(request, f'"{t.name}" is now public.')
    return redirect('tournament_manage', slug=slug)


@approved_organizer_required
@require_POST
def tournament_cancel(request, slug):
    t = _owned(request, slug)
    t.status = 'CANCELLED'
    t.save(update_fields=['status', 'updated_at'])
    messages.warning(request, f'"{t.name}" has been cancelled.')
    return redirect('organizer_dashboard')


@approved_organizer_required
@require_POST
def tournament_delete(request, slug):
    """Organizers may delete their *own* tournament, and only while it is still a
    draft — deleting one that has run would destroy results the audience saw."""
    t = _owned(request, slug)
    if t.status != 'DRAFT':
        messages.error(request, 'Only a draft can be deleted. Cancel it instead.')
        return redirect('tournament_manage', slug=slug)
    name = t.name
    t.delete()
    messages.success(request, f'Deleted draft "{name}".')
    return redirect('organizer_dashboard')


# ======================================================================
# Participants / registrations
# ======================================================================
@approved_organizer_required
def participants_manage(request, slug):
    t = _owned(request, slug)
    ctx = {'tournament': t, 'is_racket_sport': t.sport.slug in C.RACKET_SPORTS}
    if t.is_team_based:
        ctx['entries'] = t.team_entries.select_related('team').prefetch_related(
            Prefetch('team__memberships',
                     queryset=TeamMembership.objects.select_related('player__user')))
        ctx['team_form'] = TeamForm()
    else:
        ctx['registrations'] = t.registrations.select_related('player__user', 'event_category')
        ctx['entry_form'] = IndividualEntryForm(tournament=t)
    return render(request, 'organizer/participants.html', ctx)


@approved_organizer_required
@require_POST
def team_add(request, slug):
    t = _owned(request, slug)
    form = TeamForm(request.POST, request.FILES)
    if form.is_valid():
        team = form.save(commit=False)
        team.sport = t.sport
        team.save()
        TournamentTeamEntry.objects.get_or_create(tournament=t, team=team,
                                                  defaults={'status': 'APPROVED'})
        messages.success(request, f'Team "{team.name}" added.')
    else:
        messages.error(request, 'Could not add team — check the name.')
    return redirect('participants_manage', slug=slug)


@approved_organizer_required
@require_POST
def teams_bulk_import(request, slug):
    """Bulk-add teams from a CSV/.xlsx of team names — same "Add Team"
    section, using the exact Team + TournamentTeamEntry creation flow as the
    manual form above, so imported teams show up identically in the list."""
    t = _owned(request, slug)
    if not t.is_team_based:
        messages.error(request, 'Team import is only available for team-based tournaments.')
        return redirect('participants_manage', slug=slug)

    upload = request.FILES.get('teams_file')
    if not upload:
        messages.error(request, 'Choose a CSV or .xlsx file to import.')
        return redirect('participants_manage', slug=slug)

    from .team_import import parse_teams
    parsed = parse_teams(upload)

    added = 0
    for name in parsed.names:
        team = Team.objects.create(name=name, sport=t.sport)
        TournamentTeamEntry.objects.get_or_create(tournament=t, team=team,
                                                  defaults={'status': 'APPROVED'})
        added += 1

    if added:
        messages.success(request, f'Imported {added} team(s).')
    if parsed.skipped_blank:
        messages.info(request, f'{parsed.skipped_blank} empty row(s) were skipped.')
    for err in parsed.errors:
        messages.error(request, err)
    if not added and not parsed.errors:
        messages.error(request, 'No teams were imported — the file had no valid rows.')

    return redirect('participants_manage', slug=slug)


@approved_organizer_required
@require_POST
def team_member_add(request, slug, team_id):
    t = _owned(request, slug)
    team = get_object_or_404(Team, id=team_id, entries__tournament=t)
    name = (request.POST.get('display_name') or '').strip()
    if not name:
        messages.error(request, 'Enter a name for the roster entry.')
    elif _is_pair_tournament(t) and team.memberships.count() >= 2:
        messages.error(request, 'Doubles pairs are limited to 2 players.')
    else:
        TeamMembership.objects.create(team=team, display_name=name,
                                      jersey_number=request.POST.get('jersey_number', ''),
                                      is_approved=True)
        messages.success(request, f'Added {name} to {team.name}.')
    return redirect('participants_manage', slug=slug)


@approved_organizer_required
@require_POST
def team_rename(request, slug, team_id):
    t = _owned(request, slug)
    team = get_object_or_404(Team, id=team_id, entries__tournament=t)
    new_name = (request.POST.get('new_name') or '').strip()
    if not new_name:
        messages.error(request, 'Enter a team name.')
    elif len(new_name) > 120:
        messages.error(request, 'Team name is too long (max 120 characters).')
    else:
        team.name = new_name
        team.save(update_fields=['name', 'updated_at'])
        messages.success(request, f'Team renamed to "{new_name}".')
    return redirect('participants_manage', slug=slug)


@approved_organizer_required
@require_POST
def team_member_rename(request, slug, team_id, membership_id):
    t = _owned(request, slug)
    membership = get_object_or_404(TeamMembership, id=membership_id, team_id=team_id,
                                   team__entries__tournament=t)
    if membership.player_id:
        messages.error(request, 'This player is linked to an account — rename it from their profile instead.')
        return redirect('participants_manage', slug=slug)

    new_name = (request.POST.get('new_name') or '').strip()
    if not new_name:
        messages.error(request, 'Enter a player name.')
    elif len(new_name) > 120:
        messages.error(request, 'Player name is too long (max 120 characters).')
    else:
        membership.display_name = new_name
        membership.save(update_fields=['display_name'])
        messages.success(request, f'Player renamed to "{new_name}".')
    return redirect('participants_manage', slug=slug)


@approved_organizer_required
@require_POST
def team_members_bulk_import(request, slug, team_id):
    """Bulk-import a basketball roster from a CSV/.xlsx of Player Name + Jersey
    Number. Basketball-only; uses the same TeamMembership flow as manual add."""
    t = _owned(request, slug)
    if t.sport.slug != 'basketball':
        messages.error(request, 'Roster import is only available for basketball teams.')
        return redirect('participants_manage', slug=slug)

    team = get_object_or_404(Team, id=team_id, entries__tournament=t)
    upload = request.FILES.get('roster_file')
    if not upload:
        messages.error(request, 'Choose a CSV or .xlsx file to import.')
        return redirect('participants_manage', slug=slug)

    from .roster_import import parse_roster
    parsed = parse_roster(upload)

    # Skip roster names already on the team (case-insensitive), per the
    # account-less display_name model that has no DB uniqueness of its own.
    existing = {m.display_name.casefold()
                for m in team.memberships.filter(player__isnull=True)
                if m.display_name}

    added, dupes = 0, 0
    for row in parsed.members:
        if row['name'].casefold() in existing:
            dupes += 1
            continue
        TeamMembership.objects.create(team=team, display_name=row['name'],
                                      jersey_number=row['jersey'], is_approved=True)
        existing.add(row['name'].casefold())
        added += 1

    if added:
        messages.success(request, f'Imported {added} player(s) into {team.name}.')
    if dupes:
        messages.info(request, f'{dupes} player(s) already on {team.name} were skipped.')
    if parsed.skipped_blank:
        messages.info(request, f'{parsed.skipped_blank} empty row(s) were skipped.')
    for err in parsed.errors:
        messages.error(request, err)
    if not added and not parsed.errors and not dupes:
        messages.error(request, 'No players were imported — the file had no valid rows.')

    return redirect('participants_manage', slug=slug)


def _group_rows_by_team(rows):
    """Stable group-by on team name (stripped + casefolded), preserving the
    display casing of each team's first occurrence and the original row
    order within each group."""
    order = []
    groups = {}
    for row in rows:
        key = row.team_name.strip().casefold()
        if key not in groups:
            order.append(key)
            groups[key] = (row.team_name.strip(), [])
        groups[key][1].append(row)
    return [groups[key] for key in order]


@approved_organizer_required
@require_POST
def participants_bulk_import(request, slug):
    """Combined basketball import: one paste/upload creates teams and their
    rostered participants (name, jersey number, phone number) together,
    replacing the old two-step team-then-roster flow. Re-running the same
    import is safe — existing teams/participants are skipped, not duplicated."""
    t = _owned(request, slug)
    if t.sport.slug != 'basketball' and t.sport.slug not in C.RACKET_SPORTS:
        messages.error(request, 'This import is only available for basketball tournaments.')
        return redirect('participants_manage', slug=slug)

    upload = request.FILES.get('participants_file')
    pasted = (request.POST.get('participants_text') or '').strip()
    if not upload and not pasted:
        messages.error(request, 'Paste some rows or choose a file to import.')
        return redirect('participants_manage', slug=slug)

    from .participant_import import parse_participants
    parsed = parse_participants(uploaded_file=upload, pasted_text=pasted or None,
                                require_jersey=t.sport.slug == 'basketball')
    if parsed.errors:
        for e in parsed.errors:
            messages.error(request, e)
        return redirect('participants_manage', slug=slug)

    created_teams = created_participants = skipped_existing = 0
    pair_capped = 0
    is_pair = _is_pair_tournament(t)
    row_errors = []

    for team_name, rows in _group_rows_by_team(parsed.rows):
        try:
            with transaction.atomic():
                team = Team.objects.filter(sport=t.sport, entries__tournament=t,
                                           name__iexact=team_name).first()
                if team is None:
                    team = Team.objects.create(name=team_name, sport=t.sport)
                    created_teams += 1
                TournamentTeamEntry.objects.get_or_create(tournament=t, team=team,
                                                          defaults={'status': 'APPROVED'})

                for row in rows:
                    if row.error:
                        row_errors.append(f'Row {row.excel_row}: {row.error}.')
                        continue
                    existing = team.memberships.filter(
                        player__isnull=True,
                        display_name__iexact=row.participant_name).first()
                    if existing:
                        skipped_existing += 1
                        continue
                    if is_pair and team.memberships.count() >= 2:
                        pair_capped += 1
                        continue
                    TeamMembership.objects.create(team=team, display_name=row.participant_name,
                                                  jersey_number=row.jersey, phone_number=row.phone,
                                                  is_approved=True)
                    created_participants += 1
        except Exception:
            row_errors.append(f'Team "{team_name}": could not be imported (unexpected error).')

    if created_teams or created_participants:
        messages.success(request,
            f'Imported {created_participants} participant(s) across {created_teams} new team(s).')
    if skipped_existing:
        messages.info(request, f'{skipped_existing} participant(s) already existed and were skipped.')
    if pair_capped:
        messages.info(request, f'{pair_capped} participant(s) were skipped — doubles pairs are limited to 2 players.')
    if parsed.skipped_blank:
        messages.info(request, f'{parsed.skipped_blank} empty row(s) were skipped.')
    for err in row_errors[:20]:
        messages.error(request, err)
    if len(row_errors) > 20:
        messages.error(request, f'...and {len(row_errors) - 20} more row error(s).')
    if not created_teams and not created_participants and not skipped_existing and not row_errors:
        messages.error(request, 'No participants were imported — the input had no valid rows.')

    return redirect('participants_manage', slug=slug)


@approved_organizer_required
@require_POST
def member_decide(request, slug, membership_id, decision):
    t = _owned(request, slug)
    m = get_object_or_404(TeamMembership, id=membership_id, team__entries__tournament=t)
    if decision == 'approve':
        m.is_approved = True
        m.save(update_fields=['is_approved'])
        if m.player and m.player.user_id:
            Notification.push(m.player.user, f'You were added to {m.team.name} in {t.name}.',
                              url=t.get_absolute_url(), verb='team')
        messages.success(request, 'Join request approved.')
    elif decision == 'reject':
        if m.player and m.player.user_id:
            Notification.push(m.player.user,
                              f'Your request to join {m.team.name} in {t.name} was declined.',
                              url=t.get_absolute_url(), verb='team')
        m.delete()
        messages.info(request, 'Join request rejected.')
    else:
        messages.error(request, 'Unknown action.')
    return redirect('tournament_manage', slug=slug)


@approved_organizer_required
@require_POST
def individual_add(request, slug):
    t = _owned(request, slug)
    form = IndividualEntryForm(request.POST, tournament=t)
    if form.is_valid():
        reg = form.save(commit=False)
        reg.tournament = t
        reg.status = 'APPROVED'
        reg.save()
        messages.success(request, f'Registered {reg.name}.')
    else:
        messages.error(request, 'Could not add entrant — a display name is required.')
    return redirect('participants_manage', slug=slug)


@approved_organizer_required
@require_POST
def individual_bulk_import(request, slug):
    """Bulk-import solo entrants (Participant Name + optional Phone Number)
    for a racket-sport Singles/Women's tournament — the individual-
    registration counterpart to participants_bulk_import's team/pair import.
    Re-running the same import is safe: existing names are skipped."""
    t = _owned(request, slug)
    if t.sport.slug not in C.RACKET_SPORTS or t.is_team_based:
        messages.error(request, 'This import is only available for individual racket-sport tournaments.')
        return redirect('participants_manage', slug=slug)

    upload = request.FILES.get('participants_file')
    pasted = (request.POST.get('participants_text') or '').strip()
    if not upload and not pasted:
        messages.error(request, 'Paste some rows or choose a file to import.')
        return redirect('participants_manage', slug=slug)

    from .participant_import import parse_individual_participants
    parsed = parse_individual_participants(uploaded_file=upload, pasted_text=pasted or None)
    if parsed.errors:
        for e in parsed.errors:
            messages.error(request, e)
        return redirect('participants_manage', slug=slug)

    existing = {r.display_name.casefold()
                for r in t.registrations.filter(player__isnull=True) if r.display_name}
    created = skipped_existing = 0
    row_errors = []
    for row in parsed.rows:
        if row.error:
            row_errors.append(f'Row {row.excel_row}: {row.error}.')
            continue
        if row.participant_name.casefold() in existing:
            skipped_existing += 1
            continue
        IndividualRegistration.objects.create(tournament=t, display_name=row.participant_name,
                                              phone_number=row.phone, status='APPROVED')
        existing.add(row.participant_name.casefold())
        created += 1

    if created:
        messages.success(request, f'Imported {created} entrant(s).')
    if skipped_existing:
        messages.info(request, f'{skipped_existing} entrant(s) already existed and were skipped.')
    if parsed.skipped_blank:
        messages.info(request, f'{parsed.skipped_blank} empty row(s) were skipped.')
    for err in row_errors[:20]:
        messages.error(request, err)
    if len(row_errors) > 20:
        messages.error(request, f'...and {len(row_errors) - 20} more row error(s).')
    if not created and not skipped_existing and not row_errors:
        messages.error(request, 'No entrants were imported — the input had no valid rows.')
    return redirect('participants_manage', slug=slug)


@approved_organizer_required
@require_POST
def registration_rename(request, slug, reg_id):
    t = _owned(request, slug)
    reg = get_object_or_404(IndividualRegistration, id=reg_id, tournament=t)
    if reg.player_id:
        messages.error(request, 'This entrant is linked to an account — rename it from their profile instead.')
        return redirect('participants_manage', slug=slug)

    new_name = (request.POST.get('new_name') or '').strip()
    if not new_name:
        messages.error(request, 'Enter an entrant name.')
    elif len(new_name) > 120:
        messages.error(request, 'Entrant name is too long (max 120 characters).')
    else:
        reg.display_name = new_name
        reg.save(update_fields=['display_name'])
        messages.success(request, f'Entrant renamed to "{new_name}".')
    return redirect('participants_manage', slug=slug)


@approved_organizer_required
@require_POST
def entry_decide(request, slug, kind, entry_id, decision):
    """Approve / reject / remove a team entry or an individual registration."""
    t = _owned(request, slug)
    model = TournamentTeamEntry if kind == 'team' else IndividualRegistration
    entry = get_object_or_404(model, tournament=t, id=entry_id)

    if decision == 'remove':
        if t.fixtures_generated:
            messages.warning(request, 'Fixtures already exist — regenerate them after this change.')
        entry.delete()
        messages.info(request, 'Entry removed.')
    elif decision in ('approve', 'reject'):
        entry.status = 'APPROVED' if decision == 'approve' else 'REJECTED'
        entry.save(update_fields=['status'])
        user = getattr(getattr(entry, 'player', None), 'user', None)
        if user:
            verb = 'accepted' if decision == 'approve' else 'declined'
            Notification.push(user, f'Your entry to {t.name} was {verb}.',
                              url=t.get_absolute_url(), verb='registration')
        messages.success(request, f'Entry {decision}d.')
    else:
        messages.error(request, 'Unknown action.')
    return redirect('participants_manage', slug=slug)


@approved_organizer_required
@require_POST
def participants_remove_all(request, slug):
    """Bulk version of entry_decide's 'remove' — clears every team entry (or
    every individual registration) for this tournament in one go. Same
    non-destructive semantics as removing one at a time: the underlying
    Team/TeamMembership rows aren't hard-deleted, they just stop being
    associated with this tournament."""
    t = _owned(request, slug)
    if t.fixtures_generated:
        messages.warning(request, 'Fixtures already exist — regenerate them after this change.')
    if t.is_team_based:
        count = t.team_entries.count()
        t.team_entries.all().delete()
        messages.success(request, f'Removed all {count} team{"s" if count != 1 else ""}.')
    else:
        count = t.registrations.count()
        t.registrations.all().delete()
        messages.success(request, f'Removed all {count} participant{"s" if count != 1 else ""}.')
    return redirect('participants_manage', slug=slug)


# ======================================================================
# Fixtures & scheduling
# ======================================================================
# Formats where one fixture is always exactly two entrants: fixtures for these
# are now arranged by the organiser (no more Seed/Random auto-generation).
# Group-session formats (time-trial, single-event, and the esports round-robin
# lobby, where one fixture holds every entrant at once) have no 1-vs-1 shape to
# pick, so they keep using the engine's bulk generator.
_MANUAL_PAIRWISE_FORMATS = {C.FORMAT_KNOCKOUT, C.FORMAT_ROUND_ROBIN, C.FORMAT_SWISS}


def _manual_fixtures_supported(t):
    if t.format not in _MANUAL_PAIRWISE_FORMATS:
        return False
    if t.format == C.FORMAT_ROUND_ROBIN and t.sport.slug == 'mobile-esports':
        return False  # battle-royale lobby: every squad is in one fixture
    return True


def _custom_fixtures_active(t):
    """The organiser-arranged "Add fixture" flow. Available exactly where it
    always was, except while a tournament is actively running the opt-in Pool
    Stage format — switching back to Custom Fixtures restores it untouched."""
    return _manual_fixtures_supported(t) and not t.is_pool_stage


def _manual_entrant_choices(t):
    """(key, label, payload) options for the organiser's fixture picker.

    `payload` matches the shape `engines._make_participant` expects, so a
    manually created fixture is stored exactly like an engine-generated one.
    """
    choices = []
    if t.is_team_based:
        for e in t.team_entries.filter(status='APPROVED').select_related('team'):
            choices.append((f'team:{e.team_id}', e.team.name,
                            {'team': e.team, 'player': None, 'label': e.team.name, 'stats': {}}))
    else:
        for r in t.registrations.filter(status='APPROVED').select_related('player__user'):
            stats = {}
            if r.bib_number:
                stats['bib'] = r.bib_number
            if r.effective_rating:
                stats['rating'] = r.effective_rating
            if r.phone_number:
                stats['phone'] = r.phone_number
            choices.append((f'reg:{r.id}', r.name,
                            {'team': None, 'player': r.player, 'label': r.name, 'stats': stats}))
    return choices


@approved_organizer_required
@require_POST
def fixtures_generate(request, slug):
    t = _owned(request, slug)
    if t.is_pool_stage:
        messages.error(request, 'This tournament runs the Pool Stage format — use '
                                '"Generate pool fixtures" below.')
        return redirect('fixtures_manage', slug=slug)
    if _manual_fixtures_supported(t):
        messages.error(request, 'Fixtures for this tournament are created by the organiser '
                                'below — use "Add fixture".')
        return redirect('fixtures_manage', slug=slug)
    if t.participant_count() < 2:
        messages.error(request, 'Add at least two participants before generating fixtures.')
        return redirect('participants_manage', slug=slug)
    from .engines import _entrants_for
    entrants = _entrants_for(t)
    count = t.engine.generate_fixtures(entrants)
    t.fixtures_generated = True
    if t.status == 'DRAFT':
        t.status = 'PUBLISHED'
    t.save(update_fields=['fixtures_generated', 'status', 'updated_at'])
    messages.success(request, f'Generated {count} fixtures. The tournament is now public.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def fixtures_generate_bracket(request, slug):
    """Auto-generate a full single-elimination knockout bracket.

    A second, additive way to populate fixtures for a knockout-format
    tournament, alongside (not instead of) the organiser-arranged "Add
    fixture" flow below — that manual flow is untouched by this view. This
    simply exposes the existing `BracketEngine`, which already seeds any
    number of teams, auto-generates byes up to the next power of two, and
    wires round-to-round advancement, exactly as it does for every other
    knockout sport.
    """
    t = _owned(request, slug)
    if t.format != C.FORMAT_KNOCKOUT:
        messages.error(request, 'The knockout bracket generator is only available for '
                                'knockout-format tournaments.')
        return redirect('fixtures_manage', slug=slug)
    if t.is_pool_stage:
        # The pool format builds its own bracket from the pool qualifiers —
        # this generator would seed it from the raw team list and wipe the pools.
        messages.error(request, 'This tournament runs the Pool Stage format — its knockout '
                                'bracket is generated from the pool standings.')
        return redirect('fixtures_manage', slug=slug)
    if t.participant_count() < 2:
        messages.error(request, 'Add at least two participants before generating a bracket.')
        return redirect('participants_manage', slug=slug)
    from .engines import _entrants_for
    entrants = _entrants_for(t)
    count = t.engine.generate_fixtures(entrants)
    t.fixtures_generated = True
    if t.status == 'DRAFT':
        t.status = 'PUBLISHED'
    t.save(update_fields=['fixtures_generated', 'status', 'updated_at'])
    messages.success(request, f'Generated a {len(entrants)}-team knockout bracket ({count} fixtures).')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def bracket_seed_set(request, slug):
    """Save organiser-chosen seed numbers for a knockout tournament's entrants,
    then immediately (re)generate the bracket from them.

    `BracketEngine.generate_fixtures()` always pairs adjacent entrants in
    whatever list it's handed (0v1, 2v3, ...) and gives the last entrant in
    an odd-length list the bye — so both pairing styles ('1 v 2' and
    '1 v Last') are produced purely by controlling the order of the list
    passed to it here; the engine itself is never touched.
    """
    t = _owned(request, slug)
    if t.format != C.FORMAT_KNOCKOUT or t.is_pool_stage:
        messages.error(request, 'Seeding is only available for knockout-format tournaments.')
        return redirect('fixtures_manage', slug=slug)

    choices = _manual_entrant_choices(t)
    if not choices:
        messages.error(request, 'Add participants first — see Participants.')
        return redirect('fixtures_manage', slug=slug)

    from .engines import parse_seed_fields, persist_seeds, resolve_bye, order_by_seed

    seeds, err = parse_seed_fields(choices, request.POST)
    if err:
        messages.error(request, err)
        return redirect('fixtures_manage', slug=slug)

    seeds, _bye_key, err = resolve_bye(choices, seeds, request.POST)
    if err:
        messages.error(request, err)
        return redirect('fixtures_manage', slug=slug)

    persist_seeds(t, seeds)

    if t.participant_count() < 2:
        messages.error(request, 'Add at least two participants before generating a bracket.')
        return redirect('fixtures_manage', slug=slug)

    pairing_mode = request.POST.get('pairing_mode') or 'adjacent'
    entrants, final_keys = order_by_seed(choices, seeds, pairing_mode)

    count = t.engine.generate_fixtures(entrants)
    t.fixtures_generated = True
    if t.status == 'DRAFT':
        t.status = 'PUBLISHED'
    t.save(update_fields=['fixtures_generated', 'status', 'updated_at'])

    labels = {key: label for key, label, _ in choices}
    style_label = '1 v Last' if pairing_mode == 'standard' else '1 v 2'
    bye_seed_key = final_keys[-1] if len(final_keys) % 2 else None
    pairs = [f'{labels[a]} v {labels[b]}' for a, b in zip(final_keys[::2], final_keys[1::2])]
    summary = ', '.join(pairs)
    if bye_seed_key:
        summary += f'{", " if summary else ""}{labels[bye_seed_key]} has a bye'
    messages.success(request, f'Seeds saved and bracket generated ({style_label}) — '
                              f'{summary} ({count} fixtures).')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def fixture_add_manual(request, slug):
    """Organiser-arranged fixture creation: pick two entrants, create one
    fixture. Additive — existing fixtures are never cleared — so the
    organiser builds the schedule up one match at a time."""
    t = _owned(request, slug)
    if not _custom_fixtures_active(t):
        messages.error(request, 'Manual fixture creation is not available for this tournament format.')
        return redirect('fixtures_manage', slug=slug)
    if t.is_swiss and t.swiss_round:
        unfinished = t.fixtures.filter(round_no=t.swiss_round, is_removed=False).exclude(
            status__in=('COMPLETED', 'CANCELLED')).exists()
        if unfinished:
            messages.error(request, 'Finish every match in the current round before adding a fixture.')
            return redirect('fixtures_manage', slug=slug)

    key_a = (request.POST.get('entrant_a') or '').strip()
    key_b = (request.POST.get('entrant_b') or '').strip()
    if not key_a or not key_b:
        messages.error(request, 'Select both teams before creating a fixture.')
        return redirect('fixtures_manage', slug=slug)
    if key_a == key_b:
        messages.error(request, 'A team cannot play against itself.')
        return redirect('fixtures_manage', slug=slug)

    choices = {key: payload for key, _, payload in _manual_entrant_choices(t)}
    a, b = choices.get(key_a), choices.get(key_b)
    if a is None or b is None:
        messages.error(request, 'Select two valid participating teams.')
        return redirect('fixtures_manage', slug=slug)

    round_raw = (request.POST.get('round_no') or '').strip()
    round_no = int(round_raw) if round_raw.isdigit() and int(round_raw) > 0 else 1
    round_name = (request.POST.get('round_name') or '').strip() or f'Round {round_no}'

    from .engines import _make_participant
    fx = Fixture.objects.create(tournament=t, round_no=round_no, sequence=t.fixtures.count(),
                                round_name=round_name, created_by=request.user)
    _make_participant(fx, a, 0)
    _make_participant(fx, b, 1)

    t.fixtures_generated = True
    if t.status == 'DRAFT':
        t.status = 'PUBLISHED'
    t.save(update_fields=['fixtures_generated', 'status', 'updated_at'])
    messages.success(request, f'Fixture created: {a["label"]} vs {b["label"]}.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def pool_fixture_add(request, slug):
    """Add one fixture directly into an existing pool (auto-generated or
    manually created), without touching any other pool. Additive, same
    contract as `fixture_add_manual` — existing fixtures are never cleared."""
    from .pools import pool_view_context
    t = _owned(request, slug)
    if not t.is_pool_stage:
        messages.error(request, 'Switch this tournament to Pool Stage + Knockout first.')
        return redirect('fixtures_manage', slug=slug)

    pool_name = (request.POST.get('pool_name') or '').strip()
    known_pools = {p['label'] for p in pool_view_context(t)['pools']}
    if pool_name not in known_pools:
        messages.error(request, 'Unknown pool.')
        return redirect('fixtures_manage', slug=slug)

    key_a = (request.POST.get('entrant_a') or '').strip()
    key_b = (request.POST.get('entrant_b') or '').strip()
    if not key_a or not key_b:
        messages.error(request, 'Select both teams before creating a fixture.')
        return redirect('fixtures_manage', slug=slug)
    if key_a == key_b:
        messages.error(request, 'A team cannot play against itself.')
        return redirect('fixtures_manage', slug=slug)

    choices = {key: payload for key, _, payload in _manual_entrant_choices(t)}
    a, b = choices.get(key_a), choices.get(key_b)
    if a is None or b is None:
        messages.error(request, 'Select two valid participating teams.')
        return redirect('fixtures_manage', slug=slug)

    from django.db.models import Max
    from .engines import _make_participant
    prev_round = t.fixtures.filter(stage=C.STAGE_POOL, pool_name=pool_name).aggregate(
        m=Max('round_no'))['m'] or 0
    round_no = prev_round + 1
    fx = Fixture.objects.create(
        tournament=t, round_no=round_no, sequence=t.fixtures.count(),
        round_name=f'Pool {pool_name} · Round {round_no}',
        stage=C.STAGE_POOL, pool_name=pool_name, created_by=request.user)
    _make_participant(fx, a, 0)
    _make_participant(fx, b, 1)

    t.fixtures_generated = True
    if t.status == 'DRAFT':
        t.status = 'PUBLISHED'
    t.save(update_fields=['fixtures_generated', 'status', 'updated_at'])
    t.engine.compute_standings()
    messages.success(request, f'Fixture added to Pool {pool_name}: {a["label"]} vs {b["label"]}.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def pool_fixture_build(request, slug):
    """"Fixture creation by pool": (re)build one pool's round-robin schedule
    from an explicit entrant list + pairing mode. Any selected entrant
    currently sitting in a *different* pool is moved here, and that other
    pool's schedule is rebuilt too (see
    PoolKnockoutEngine.rebuild_pool_membership) — every pool not involved in
    the move is left untouched."""
    t = _owned(request, slug)
    if not t.is_pool_stage:
        messages.error(request, 'Switch this tournament to Pool Stage + Knockout first.')
        return redirect('fixtures_manage', slug=slug)

    pool_label_in = (request.POST.get('pool_label') or '').strip()
    if not pool_label_in:
        messages.error(request, 'Enter a pool name.')
        return redirect('fixtures_manage', slug=slug)
    if len(pool_label_in) > 40:
        messages.error(request, 'Pool name is too long (max 40 characters).')
        return redirect('fixtures_manage', slug=slug)

    entrant_ids = request.POST.getlist('entrant_ids')
    if len(entrant_ids) < 2:
        messages.error(request, 'Select at least two entrants for this pool.')
        return redirect('fixtures_manage', slug=slug)

    valid_keys = {key for key, _label, _payload in _manual_entrant_choices(t)}
    if not set(entrant_ids) <= valid_keys:
        messages.error(request, 'One or more selected entrants are no longer valid.')
        return redirect('fixtures_manage', slug=slug)

    pairing_mode = request.POST.get('pairing_mode') or 'adjacent'
    moves = {key: pool_label_in for key in entrant_ids}
    affected = t.engine.rebuild_pool_membership(moves, pairing_mode=pairing_mode)

    t.fixtures_generated = True
    if t.status == 'DRAFT':
        t.status = 'PUBLISHED'
    t.save(update_fields=['fixtures_generated', 'status', 'updated_at'])

    others = sorted(affected - {pool_label_in})
    msg = f'Pool {pool_label_in} fixtures generated ({len(entrant_ids)} entrants).'
    if others:
        msg += f' Also rebuilt: {", ".join(others)} (entrant(s) moved out).'
    messages.success(request, msg)
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def pool_create(request, slug):
    """Create a new, initially empty pool that the organiser can add
    fixtures to via `pool_fixture_add`. Never touches existing pools."""
    from .pools import pool_view_context
    t = _owned(request, slug)
    if not t.is_pool_stage:
        messages.error(request, 'Switch this tournament to Pool Stage + Knockout first.')
        return redirect('fixtures_manage', slug=slug)

    name = (request.POST.get('pool_name') or '').strip()
    if not name:
        messages.error(request, 'Enter a pool name.')
        return redirect('fixtures_manage', slug=slug)

    known_pools = {p['label'] for p in pool_view_context(t)['pools']}
    if name.lower() in {label.lower() for label in known_pools}:
        messages.error(request, f'A pool named "{name}" already exists.')
        return redirect('fixtures_manage', slug=slug)

    from .pools import append_pool_order
    cfg = dict(t.pool_config or {})
    extra = list(cfg.get('extra_labels') or [])
    extra.append(name)
    cfg['extra_labels'] = extra
    append_pool_order(cfg, name)
    t.pool_config = cfg
    t.save(update_fields=['pool_config', 'updated_at'])
    messages.success(request, f'Pool "{name}" created — add fixtures to it below.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def pool_reorder(request, slug):
    """Persist the organizer's drag-and-drop pool order — a full replacement
    list of every current pool label, in the new display order."""
    from .pools import pool_view_context
    t = _owned(request, slug)
    if not t.is_pool_stage:
        messages.error(request, 'This tournament does not run the Pool Stage format.')
        return redirect('fixtures_manage', slug=slug)

    new_order = [l.strip() for l in request.POST.getlist('label') if l.strip()]
    known_pools = {p['label'] for p in pool_view_context(t)['pools']}
    if set(new_order) != known_pools:
        messages.error(request, 'Pool order is out of date — reload and try again.')
        return redirect('fixtures_manage', slug=slug)

    cfg = dict(t.pool_config or {})
    cfg['order'] = new_order
    t.pool_config = cfg
    t.save(update_fields=['pool_config', 'updated_at'])
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def pool_rename(request, slug):
    """Rename an existing pool — updates its fixtures, standings, and
    denormalised team-entry pool membership in one go, wherever the sport
    (basketball, currently the only one with pools) is displaying it."""
    from .pools import pool_view_context, rename_pool
    t = _owned(request, slug)
    if not t.is_pool_stage:
        messages.error(request, 'This tournament does not run the Pool Stage format.')
        return redirect('fixtures_manage', slug=slug)

    old_label = (request.POST.get('old_label') or '').strip()
    new_label = (request.POST.get('new_label') or '').strip()
    if not new_label:
        messages.error(request, 'Enter a pool name.')
        return redirect('fixtures_manage', slug=slug)
    if len(new_label) > 40:
        messages.error(request, 'Pool name is too long (max 40 characters).')
        return redirect('fixtures_manage', slug=slug)

    known_pools = {p['label'] for p in pool_view_context(t)['pools']}
    if old_label not in known_pools:
        messages.error(request, 'That pool no longer exists.')
        return redirect('fixtures_manage', slug=slug)
    if new_label != old_label and new_label.lower() in {label.lower() for label in known_pools}:
        messages.error(request, f'A pool named "{new_label}" already exists.')
        return redirect('fixtures_manage', slug=slug)

    if new_label != old_label:
        rename_pool(t, old_label, new_label)
        messages.success(request, f'Pool renamed to "{new_label}".')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def knockout_round_rename(request, slug):
    """Rename a pool-derived knockout round (Quarterfinal/Semifinal/Final,
    or any custom name an organizer already gave it) — updates every fixture
    sharing that round_no. Rounds are identified by round_no, not name, so
    unlike pool_rename there is no uniqueness check to make."""
    t = _owned(request, slug)
    if not t.is_pool_stage:
        messages.error(request, 'This tournament does not run the Pool Stage format.')
        return redirect('fixtures_manage', slug=slug)

    round_raw = (request.POST.get('round_no') or '').strip()
    new_name = (request.POST.get('new_name') or '').strip()
    if not new_name:
        messages.error(request, 'Enter a round name.')
        return redirect('fixtures_manage', slug=slug)
    if len(new_name) > 40:
        messages.error(request, 'Round name is too long (max 40 characters).')
        return redirect('fixtures_manage', slug=slug)
    if not round_raw.isdigit():
        messages.error(request, 'That round no longer exists.')
        return redirect('fixtures_manage', slug=slug)

    round_fixtures = t.fixtures.filter(stage=C.STAGE_KNOCKOUT, round_no=int(round_raw), is_removed=False)
    if not round_fixtures.exists():
        messages.error(request, 'That round no longer exists.')
        return redirect('fixtures_manage', slug=slug)

    round_fixtures.update(round_name=new_name)
    messages.success(request, f'Round renamed to "{new_name}".')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def knockout_round_fixture_add(request, slug):
    """Add one extra, standalone fixture into an existing pool-derived
    knockout round (e.g. a replacement or 3rd-place match) — same "additive,
    never auto-clears" contract as pool_fixture_add. It never wires
    advances_to/advances_slot, so it never joins the bracket's own
    auto-advancement — the organizer records its result manually."""
    t = _owned(request, slug)
    if not t.is_pool_stage:
        messages.error(request, 'Switch this tournament to Pool Stage + Knockout first.')
        return redirect('fixtures_manage', slug=slug)

    round_raw = (request.POST.get('round_no') or '').strip()
    if not round_raw.isdigit():
        messages.error(request, 'That round no longer exists.')
        return redirect('fixtures_manage', slug=slug)
    round_no = int(round_raw)
    round_fixtures = list(t.fixtures.filter(stage=C.STAGE_KNOCKOUT, round_no=round_no, is_removed=False))
    if not round_fixtures:
        messages.error(request, 'That round no longer exists.')
        return redirect('fixtures_manage', slug=slug)
    round_name = round_fixtures[0].round_name

    key_a = (request.POST.get('entrant_a') or '').strip()
    key_b = (request.POST.get('entrant_b') or '').strip()
    if not key_a or not key_b:
        messages.error(request, 'Select both teams before creating a fixture.')
        return redirect('fixtures_manage', slug=slug)
    if key_a == key_b:
        messages.error(request, 'A team cannot play against itself.')
        return redirect('fixtures_manage', slug=slug)

    choices = {key: payload for key, _, payload in _manual_entrant_choices(t)}
    a, b = choices.get(key_a), choices.get(key_b)
    if a is None or b is None:
        messages.error(request, 'Select two valid participating teams.')
        return redirect('fixtures_manage', slug=slug)

    from .engines import _make_participant
    next_position = max((f.bracket_position or 0) for f in round_fixtures) + 1
    fx = Fixture.objects.create(
        tournament=t, round_no=round_no, sequence=t.fixtures.count(), bracket_position=next_position,
        round_name=round_name, stage=C.STAGE_KNOCKOUT, created_by=request.user)
    _make_participant(fx, a, 0)
    _make_participant(fx, b, 1)

    t.fixtures_generated = True
    if t.status == 'DRAFT':
        t.status = 'PUBLISHED'
    t.save(update_fields=['fixtures_generated', 'status', 'updated_at'])
    messages.success(request, f'Fixture added to {round_name}: {a["label"]} vs {b["label"]}.')
    return redirect('fixtures_manage', slug=slug)


def _knockout_bracket_context(fixtures):
    """Group a knockout tournament's fixtures into bracket_rounds + champion —
    same shape `public/_bracket.html` already renders on the public detail
    page, reused here so the organiser sees the same bracket while managing
    fixtures. Works for fixtures from either the bracket generator or the
    manual "Add fixture" flow, since both just need round_no/round_name.
    """
    rounds = {}
    for fx in sorted(fixtures, key=lambda f: (f.round_no, f.sequence)):
        rounds.setdefault(fx.round_no, {'name': fx.round_name, 'fixtures': []})
        rounds[fx.round_no]['fixtures'].append(fx)
    bracket_rounds = [rounds[k] for k in sorted(rounds)]
    champion = None
    if rounds:
        final = rounds[max(rounds)]['fixtures']
        if len(final) == 1 and final[0].status == 'COMPLETED':
            champion = next((p for p in final[0].participants.all() if p.is_winner), None)
    return bracket_rounds, champion


@approved_organizer_required
def fixtures_manage(request, slug):
    from .pools import pool_label
    t = _owned(request, slug)
    fixtures = t.fixtures.filter(is_removed=False).prefetch_related(
        Prefetch('participants',
                 queryset=FixtureParticipant.objects.select_related('team', 'player__user')))
    manual_mode = _manual_fixtures_supported(t)
    custom_mode = _custom_fixtures_active(t)
    pool_mode = t.is_pool_stage
    is_knockout = t.format == C.FORMAT_KNOCKOUT and not pool_mode
    is_swiss = t.is_swiss
    num_pools, teams_per_pool, qualifiers_per_pool = t.pool_settings
    ctx = {
        'tournament': t, 'fixtures': fixtures,
        'manual_mode': manual_mode,
        'custom_mode': custom_mode,
        'entrant_choices': _manual_entrant_choices(t) if (custom_mode or pool_mode) else [],
        'existing_rounds': (sorted(set(fixtures.values_list('round_no', flat=True)))
                            if custom_mode else []),
        # Feeds the "Add a fixture" round-column board so a pair created
        # there shows up inside its own round column immediately, not just
        # in the match-centre list further down the page.
        'existing_fixtures_by_round': ([
            {'round_no': fx.round_no, 'a': parts[0].name, 'b': parts[1].name}
            for fx in fixtures for parts in [list(fx.participants.all())] if len(parts) == 2
        ] if custom_mode else []),
        'is_knockout': is_knockout,
        'is_swiss': is_swiss,
        # Pool Stage + Knockout (basketball only — see Tournament.supports_pool_stage)
        'pool_supported': t.supports_pool_stage,
        'pool_mode': pool_mode,
        'team_total': t.participant_count(),
        'pool_form': {'num_pools': num_pools or '', 'teams_per_pool': teams_per_pool or '',
                      'qualifiers_per_pool': qualifiers_per_pool or ''},
        'pool_assignment_mode': t.pool_assignment_mode,
        'pool_entrants': ([{'key': f'team:{e.team_id}', 'name': e.team.name, 'seed': e.seed,
                            'pool': t.pool_assignments.get(f'team:{e.team_id}', '')}
                           for e in t.team_entries.filter(status='APPROVED').select_related('team')]
                          if pool_mode and t.is_team_based
                          else [{'key': f'reg:{r.id}', 'name': r.name, 'seed': r.seed,
                                'pool': t.pool_assignments.get(f'reg:{r.id}', '')}
                               for r in t.registrations.filter(status='APPROVED')]
                          if pool_mode else []),
        'pool_labels': [pool_label(i) for i in range(max(num_pools, 2))] if pool_mode else [],
        'bracket_entrants': ([{'key': f'team:{e.team_id}', 'name': e.team.name, 'seed': e.seed}
                              for e in t.team_entries.filter(status='APPROVED').select_related('team')]
                             if is_knockout and t.is_team_based
                             else [{'key': f'reg:{r.id}', 'name': r.name, 'seed': r.seed}
                                   for r in t.registrations.filter(status='APPROVED')]
                             if is_knockout else []),
    }
    if is_knockout:
        bracket_rounds, champion = _knockout_bracket_context(list(fixtures))
        ctx['bracket_rounds'] = bracket_rounds
        ctx['champion'] = champion
    if pool_mode:
        from .pools import pool_view_context, qualified_choices
        ctx.update(pool_view_context(t))
        # Guards the rename-pool button in the shared public/_pool_standings.html
        # partial — that template also renders on the public tournament page,
        # which must never get an organizer-only edit affordance.
        ctx['pool_names_editable'] = True
        if ctx.get('pool_stage_complete'):
            team_seeds = {e.team_id: e.seed for e in t.team_entries.all()}
            reg_seeds = {r.id: r.seed for r in t.registrations.all()}

            def _bracket_seed(key):
                kind, _, raw_id = key.partition(':')
                if kind == 'team':
                    return team_seeds.get(int(raw_id))
                if kind == 'reg':
                    return reg_seeds.get(int(raw_id))
                return None

            ctx['pool_bracket_entrants'] = [
                {'key': key, 'name': label, 'seed': _bracket_seed(key)}
                for key, label, _payload in qualified_choices(t.engine)
            ]
        else:
            ctx['pool_bracket_entrants'] = []
    if is_swiss:
        import math
        current_round = t.swiss_round
        current_fixtures = t.fixtures.filter(round_no=current_round, is_removed=False) if current_round else t.fixtures.none()
        entrant_total = t.participant_count()
        ctx.update({
            'swiss_round': current_round,
            'swiss_num_rounds': t.swiss_num_rounds,
            'swiss_suggested_rounds': max(1, math.ceil(math.log2(entrant_total))) if entrant_total > 1 else 1,
            'swiss_current_round_played': current_fixtures.filter(status='COMPLETED').count(),
            'swiss_current_round_total': current_fixtures.count(),
            'swiss_current_round_complete': bool(current_round) and not current_fixtures.exclude(
                status__in=('COMPLETED', 'CANCELLED')).exists(),
            'swiss_can_generate_next': bool(current_round) and (
                not t.swiss_num_rounds or current_round < t.swiss_num_rounds),
            'swiss_standings': list(t.standings.all().order_by('position')),
        })
    if t.sport.slug == 'chess' and t.format == C.FORMAT_ROUND_ROBIN:
        # Round-robin is chess's default format — same rating-ordered points
        # table as Swiss (see PointsTableEngine.compute_standings), just
        # generated upfront instead of round by round.
        ctx['chess_standings'] = list(t.standings.all().order_by('position'))
    return render(request, 'organizer/fixtures.html', ctx)


# ======================================================================
# Pool Stage + Knockout (basketball only)
# ======================================================================
def _pool_int(request, field):
    raw = (request.POST.get(field) or '').strip()
    return int(raw) if raw.isdigit() else 0


@approved_organizer_required
@require_POST
def fixture_mode_set(request, slug):
    """Switch a tournament between Custom Fixtures and Pool Stage + Knockout.

    Only ever flips the flag — no fixture is created, deleted or altered here,
    so an organizer can look at the other option and switch straight back.
    """
    t = _owned(request, slug)
    if not t.supports_pool_stage:
        messages.error(request, 'This sport only supports Custom Fixtures.')
        return redirect('fixtures_manage', slug=slug)
    mode = request.POST.get('fixture_mode')
    if mode not in (C.FIXTURE_MODE_CUSTOM, C.FIXTURE_MODE_POOL):
        messages.error(request, 'Choose a fixture type.')
        return redirect('fixtures_manage', slug=slug)
    if mode == t.fixture_mode:
        return redirect('fixtures_manage', slug=slug)
    t.fixture_mode = mode
    t.save(update_fields=['fixture_mode', 'updated_at'])
    label = dict(C.FIXTURE_MODE_CHOICES)[mode]
    messages.success(request, f'Fixture type set to {label}. Existing fixtures were left as they are.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def pool_setup(request, slug):
    """Save the pool configuration, and optionally generate the pool fixtures.

    Validation is server-side and authoritative: pools × entrants-per-pool
    must equal the approved entrant count, and nothing is generated until it
    does.
    """
    from .pools import PoolConfigError, validate_manual_pools, validate_pool_config
    t = _owned(request, slug)
    if not t.is_pool_stage:
        messages.error(request, 'Switch this tournament to Pool Stage + Knockout first.')
        return redirect('fixtures_manage', slug=slug)

    manual = request.POST.get('assignment_mode') == 'manual'
    num_pools = _pool_int(request, 'num_pools')
    qualifiers_per_pool = _pool_int(request, 'qualifiers_per_pool')
    # Manually-created empty pools (added via the "Create another pool" popup)
    # survive a routine settings save — they're only reset when the organizer
    # actually (re)generates, below.
    existing_extra = (t.pool_config or {}).get('extra_labels') or []

    if manual:
        assignments = {}
        for key, _label, _payload in _manual_entrant_choices(t):
            pool_label_for_entrant = (request.POST.get(f'pool_for_{key}') or '').strip()
            if pool_label_for_entrant:
                assignments[key] = pool_label_for_entrant
        # Remember what the organizer picked even when it does not validate,
        # so the dialog comes back with the same selections rather than blank.
        t.pool_config = {'num_pools': num_pools, 'qualifiers_per_pool': qualifiers_per_pool,
                         'assignment_mode': 'manual', 'assignments': assignments,
                         'extra_labels': existing_extra}
        t.save(update_fields=['pool_config', 'updated_at'])

        from .engines import _entrants_for
        ok, message = validate_manual_pools(assignments, num_pools, qualifiers_per_pool,
                                            _entrants_for(t))
    else:
        teams_per_pool = _pool_int(request, 'teams_per_pool')
        # Remember what the organizer typed even when it does not validate, so the
        # form comes back filled in rather than blank.
        t.pool_config = {'num_pools': num_pools, 'teams_per_pool': teams_per_pool,
                         'qualifiers_per_pool': qualifiers_per_pool, 'assignment_mode': 'auto',
                         'extra_labels': existing_extra}
        t.save(update_fields=['pool_config', 'updated_at'])

        total = t.participant_count()
        ok, message = validate_pool_config(num_pools, teams_per_pool, qualifiers_per_pool, total)

    if not ok:
        messages.error(request, message)
        return redirect('fixtures_manage', slug=slug)

    if request.POST.get('action') != 'generate':
        messages.success(request, 'Pool setup saved.')
        return redirect('fixtures_manage', slug=slug)

    from .engines import parse_seed_fields, persist_seeds
    choices = _manual_entrant_choices(t)
    seeds, err = parse_seed_fields(choices, request.POST)
    if err:
        messages.error(request, err)
        return redirect('fixtures_manage', slug=slug)
    persist_seeds(t, seeds)
    pairing_mode = request.POST.get('pairing_mode') or 'adjacent'

    try:
        count = t.engine.generate_fixtures(pairing_mode=pairing_mode)
    except PoolConfigError as exc:
        messages.error(request, str(exc))
        return redirect('fixtures_manage', slug=slug)
    # A fresh generate wipes every fixture — any leftover manually-created
    # empty pool should reset along with it.
    t.pool_config = {**t.pool_config, 'extra_labels': []}
    t.fixtures_generated = True
    if t.status == 'DRAFT':
        t.status = 'PUBLISHED'
    t.save(update_fields=['fixtures_generated', 'status', 'pool_config', 'updated_at'])
    messages.success(
        request, f'Generated {count} pool fixtures across {num_pools} pools. '
                 f'The knockout bracket appears automatically once every pool match is final.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def pool_knockout_generate(request, slug):
    """Rebuild the knockout bracket from the current pool standings.

    The bracket is normally created automatically the moment the last pool
    match is finalized. This is the organizer's explicit redo, for when a pool
    result was corrected after the fact — it replaces any knockout fixtures
    (and their scores), which the button confirms before posting.
    """
    t = _owned(request, slug)
    if not t.is_pool_stage:
        messages.error(request, 'This tournament does not run the Pool Stage format.')
        return redirect('fixtures_manage', slug=slug)
    engine = t.engine
    if not engine.pool_stage_complete():
        messages.error(request, 'Finish every pool match before generating the knockout bracket.')
        return redirect('fixtures_manage', slug=slug)

    from .pools import qualified_choices
    from .engines import parse_seed_fields, persist_seeds, resolve_bye, order_by_seed

    choices = qualified_choices(engine)
    if not choices:
        messages.error(request, 'Not enough qualified teams to build a knockout bracket.')
        return redirect('fixtures_manage', slug=slug)

    seeds, err = parse_seed_fields(choices, request.POST)
    if err:
        messages.error(request, err)
        return redirect('fixtures_manage', slug=slug)

    seeds, _bye_key, err = resolve_bye(choices, seeds, request.POST)
    if err:
        messages.error(request, err)
        return redirect('fixtures_manage', slug=slug)

    persist_seeds(t, seeds)
    pairing_mode = request.POST.get('pairing_mode') or 'adjacent'
    order, _ = order_by_seed(choices, seeds, pairing_mode)

    count = engine.generate_knockout(force=True, manual_order=order)
    if count:
        messages.success(request, f'Knockout bracket generated — {count} fixtures.')
    else:
        messages.error(request, 'Not enough qualified teams to build a knockout bracket.')
    return redirect('fixtures_manage', slug=slug)


# ======================================================================
# Swiss format (chess)
# ======================================================================
@approved_organizer_required
@require_POST
def swiss_setup(request, slug):
    """Save the Swiss round count, and generate Round 1 if requested. Round
    2 onward always goes through swiss_generate_round instead — pairing
    only makes sense once the previous round's results exist."""
    t = _owned(request, slug)
    if not t.is_swiss:
        messages.error(request, 'This tournament does not run the Swiss format.')
        return redirect('fixtures_manage', slug=slug)

    num_rounds = _pool_int(request, 'num_rounds')
    if num_rounds < 1:
        messages.error(request, 'Enter at least 1 round.')
        return redirect('fixtures_manage', slug=slug)

    t.swiss_config = {**(t.swiss_config or {}), 'num_rounds': num_rounds}
    t.save(update_fields=['swiss_config', 'updated_at'])

    if request.POST.get('action') != 'generate':
        messages.success(request, 'Swiss setup saved.')
        return redirect('fixtures_manage', slug=slug)

    if t.swiss_round:
        messages.error(request, 'Round 1 has already been generated — use "Generate next round" instead.')
        return redirect('fixtures_manage', slug=slug)

    count = t.engine.generate_fixtures()
    if count:
        t.fixtures_generated = True
        if t.status == 'DRAFT':
            t.status = 'PUBLISHED'
        t.save(update_fields=['fixtures_generated', 'status', 'updated_at'])
        messages.success(request, f'Round 1 generated ({count} fixtures).')
    else:
        messages.error(request, 'Add at least two participants before generating Round 1.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def swiss_generate_round(request, slug):
    """Pair and create the next Swiss round from current standings. Only
    possible once every fixture in the current round is decided, and only
    up to the configured number of rounds."""
    t = _owned(request, slug)
    if not t.is_swiss:
        messages.error(request, 'This tournament does not run the Swiss format.')
        return redirect('fixtures_manage', slug=slug)
    if not t.swiss_round:
        messages.error(request, 'Generate Round 1 first.')
        return redirect('fixtures_manage', slug=slug)
    if t.swiss_num_rounds and t.swiss_round >= t.swiss_num_rounds:
        messages.error(request, 'Every configured round has already been generated.')
        return redirect('fixtures_manage', slug=slug)

    count = t.engine.generate_next_round()
    if count:
        messages.success(request, f'Round {t.swiss_round} generated ({count} fixtures).')
    else:
        messages.error(request, 'Finish every match in the current round before generating the next one.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
def chess_results_export(request, slug):
    """Download fixtures as a CSV: round, White, Black, and the result (the
    winner's name, or 'Draw', blank if not yet decided). A plain GET
    download, same pattern as dash/views.py's admin CSV export — no state
    change, so no CSRF/POST needed. Optional ?round=<n> limits the file to
    a single round, for the per-round download icon on the fixtures page."""
    t = _owned(request, slug)
    if t.sport.slug != 'chess':
        messages.error(request, 'CSV export is only available for chess tournaments.')
        return redirect('fixtures_manage', slug=slug)

    fixtures = t.fixtures.filter(is_removed=False).order_by('round_no', 'sequence', 'id')
    round_no = request.GET.get('round')
    suffix = ''
    if round_no and round_no.isdigit():
        fixtures = fixtures.filter(round_no=int(round_no))
        suffix = f'-round-{round_no}'

    response = HttpResponse(content_type='text/csv')
    stamp = timezone.localtime(timezone.now()).strftime('%Y%m%d-%H%M')
    response['Content-Disposition'] = f'attachment; filename="{t.slug}-chess-results{suffix}-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Round', 'White', 'Black', 'Result'])
    for fx in fixtures:
        parts = list(fx.ordered_participants())
        white = parts[0].name if parts else ''
        black = parts[1].name if len(parts) > 1 else ''
        result = ''
        if fx.status == 'COMPLETED' and len(parts) == 2:
            # Score, not is_winner: the round-robin engine (chess's default
            # format) never sets is_winner on FixtureParticipant, only score
            # — 1/0 for a decisive game, 0.5/0.5 for a draw.
            s0, s1 = parts[0].score, parts[1].score
            if s0 is not None and s1 is not None and s0 != s1:
                result = parts[0].name if s0 > s1 else parts[1].name
            else:
                result = 'Draw'
        writer.writerow([fx.round_no, white, black, result])
    return response


@approved_organizer_required
def participant_search(request):
    """Name-search across this organizer's own past individual-registration
    entrants (any of their tournaments), for the "Add an Entrant" form's
    autocomplete — suggests a rating/phone to reuse instead of retyping."""
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
    qs = (IndividualRegistration.objects
          .filter(tournament__organizer=request.user.organizer_profile, display_name__icontains=q)
          .order_by('-registered_at')[:200])
    seen, results = set(), []
    for r in qs:
        key = r.display_name.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        results.append({'name': r.display_name, 'rating': r.rating, 'phone': r.phone_number})
        if len(results) >= 10:
            break
    return JsonResponse({'results': results})


@approved_organizer_required
@require_POST
def fixture_delete(request, slug, fixture_id):
    """Remove a single fixture from the Fixtures & Scoring list.

    Soft-delete via the existing `Fixture.is_removed` flag — the same
    mechanism every fixture query in the app already filters on (the fixture
    list, live-score ticker, and standings), so removal is immediate and
    complete everywhere without touching any other fixture, team, player, or
    the tournament itself.
    """
    t = _owned(request, slug)
    fixture = get_object_or_404(Fixture, id=fixture_id, tournament=t, is_removed=False)
    fixture.is_removed = True
    fixture.save(update_fields=['is_removed', 'updated_at'])
    engine = t.engine
    engine.compute_standings()  # no-op for formats without a table; refreshes round-robin
    if t.is_pool_stage and fixture.stage == C.STAGE_POOL:
        # Dropping the last outstanding pool match finishes the pool stage just
        # as finalizing it would, so the bracket should appear here too.
        engine.generate_knockout()
    messages.success(request, 'Fixture deleted.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def fixtures_clear_all(request, slug):
    """Bulk version of fixture_delete: remove every already-generated fixture
    for this tournament in one go, regardless of how they were created
    (engine bulk-generate, the knockout bracket generator, or added one at a
    time by the organiser). Same soft-delete mechanism as deleting a single
    fixture, just applied to all of them, so every other view/query that
    already filters on `is_removed` picks the change up automatically.
    """
    t = _owned(request, slug)
    count = t.fixtures.filter(is_removed=False).update(is_removed=True, updated_at=timezone.now())
    if count:
        t.fixtures_generated = False
        t.save(update_fields=['fixtures_generated', 'updated_at'])
        t.engine.compute_standings()  # no-op for formats without a table; clears round-robin
        messages.success(request, f'Removed {count} fixture(s). Generate or add fixtures to start over.')
    else:
        messages.info(request, 'There are no fixtures to remove.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
def fixture_schedule(request, slug, fixture_id):
    """Set kick-off time and court/lane for one fixture."""
    t = _owned(request, slug)
    fixture = get_object_or_404(Fixture, id=fixture_id, tournament=t)
    form = FixtureScheduleForm(request.POST or None, instance=fixture)
    if request.method == 'POST' and form.is_valid():
        form.save()
        for p in fixture.participants.select_related('player__user'):
            if p.player and p.player.user_id and fixture.scheduled_time:
                Notification.push(
                    p.player.user,
                    f'{t.name}: your match is scheduled for '
                    f'{timezone.localtime(fixture.scheduled_time):%d %b, %H:%M}.',
                    url=fixture.get_absolute_url(), verb='schedule')
        messages.success(request, 'Schedule updated.')
        return redirect('fixtures_manage', slug=slug)
    return render(request, 'organizer/schedule.html',
                  {'tournament': t, 'fixture': fixture, 'form': form})


# ======================================================================
# Live scoring
# ======================================================================
_BASKETBALL_POINT_VALUES = {'1', '2', '3'}


def _basketball_scoring_context(fixture):
    """Player choices per team-side and the individual scoring+fouls table —
    all derived from existing data (TeamMembership rosters, ScoreEvent rows),
    so nothing here needs a new source of truth beyond what's already
    persisted.
    """
    participants = list(fixture.participants.select_related('team').all())
    player_choices = {
        p.id: (list(p.team.memberships.filter(is_approved=True).order_by('jersey_number'))
               if p.team_id else [])
        for p in participants
    }

    totals = {}

    def row_for(mid, snap, participant):
        return totals.setdefault(mid, {
            'name': snap.get('player_name', ''), 'jersey_number': snap.get('jersey_number', ''),
            'team_name': participant.name if participant else '',
            'team_participant_id': participant.id if participant else None,
            'pt1': 0, 'pt2': 0, 'pt3': 0, 'total': 0, 'fouls': 0,
        })

    for ev in fixture.events.filter(event_type='score').select_related('participant__team'):
        snap = ev.score_snapshot or {}
        mid = snap.get('membership_id')
        pts = int(snap.get('points') or 0)
        if not mid or pts not in (1, 2, 3):
            continue
        row = row_for(mid, snap, ev.participant)
        row[f'pt{pts}'] += 1
        row['total'] += pts

    for ev in fixture.events.filter(event_type='foul').select_related('participant__team'):
        snap = ev.score_snapshot or {}
        mid = snap.get('membership_id')
        if not mid:
            continue
        row = row_for(mid, snap, ev.participant)
        row['fouls'] += 1

    individual_rows = sorted(totals.values(), key=lambda r: (-r['total'], -r['fouls'], r['name'].lower()))
    # Same rows, split per team-side — the redesigned individual-scoring panel
    # (score.html) shows one team's players at a time and needs its own list
    # + count per participant without re-deriving anything from the events.
    individual_rows_by_team = {}
    for row in individual_rows:
        individual_rows_by_team.setdefault(row['team_participant_id'], []).append(row)
    return player_choices, individual_rows, individual_rows_by_team


@approved_organizer_required
def score_fixture(request, slug, fixture_id):
    t = _owned(request, slug)
    fixture = get_object_or_404(Fixture, id=fixture_id, tournament=t)
    participants = list(fixture.ordered_participants())
    is_basketball = t.sport.slug == 'basketball'
    is_racket = t.sport.slug in C.RACKET_SPORTS
    is_chess = t.sport.slug == 'chess'

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'start':
            fixture.status = 'LIVE'
            update_fields = ['status', 'updated_at']
            if not fixture.live_started_at:
                now = timezone.now()
                fixture.live_started_at = now
                update_fields.append('live_started_at')
                if is_basketball:
                    # Settings chosen on the pre-match setup screen (see the
                    # 'elif is_basketball' branch of score.html, only shown
                    # while SCHEDULED) — validated against the options that
                    # screen actually offers, falling back to the model
                    # defaults for any stray/tampered value.
                    quarter_minutes_raw = request.POST.get('quarter_minutes')
                    quarter_minutes = int(quarter_minutes_raw) if quarter_minutes_raw in ('10', '12', '24') else 10
                    fixture.quarter_length_seconds = quarter_minutes * 60
                    update_fields.append('quarter_length_seconds')

                    shot_seconds_raw = request.POST.get('shot_clock_seconds')
                    shot_seconds = int(shot_seconds_raw) if shot_seconds_raw in ('12', '24') else 24
                    fixture.shot_clock_duration_seconds = shot_seconds
                    update_fields.append('shot_clock_duration_seconds')

                    fixture.individual_scoring_enabled = bool(request.POST.get('individual_scoring'))
                    update_fields.append('individual_scoring_enabled')

                    # The quarter clock (and the shot clock, which mirrors it)
                    # starts paused (frozen at the full quarter/shot length)
                    # until the organizer explicitly taps Play — it should
                    # never auto-tick just because the match went LIVE.
                    fixture.clock_paused_at = now
                    update_fields.append('clock_paused_at')
                    fixture.shot_clock_seconds_remaining = fixture.shot_clock_duration_seconds
                    fixture.shot_clock_running_since = None
                    update_fields += ['shot_clock_seconds_remaining', 'shot_clock_running_since']
                elif is_racket:
                    # Best-of-3 (default) or best-of-5, chosen on the
                    # pre-match setup screen (score.html's 'elif is_racket'
                    # branch, only shown while SCHEDULED).
                    best_of = request.POST.get('best_of')
                    fixture.sets_to_win = 3 if best_of == '5' else 2
                    fixture.set_scores = [{'a': 0, 'b': 0}]
                    update_fields += ['sets_to_win', 'set_scores']
            fixture.save(update_fields=update_fields)
            sync_tournament_status(t)
            messages.info(request, 'Match marked LIVE.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'youtube':
            fixture.youtube_url = (request.POST.get('youtube_url') or '').strip()
            fixture.save(update_fields=['youtube_url', 'updated_at'])
            messages.success(request, 'Stream link updated.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'commentary':
            text = (request.POST.get('commentary') or '').strip()
            if text:
                ScoreEvent.objects.create(fixture=fixture, description=text[:280],
                                          created_by=request.user, event_type='note')
                messages.success(request, 'Commentary posted.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'extra_time':
            if not is_basketball:
                messages.error(request, 'Extra time is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            if fixture.status != 'LIVE':
                messages.error(request, 'Start the match before adding extra time.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            raw = (request.POST.get('extra_minutes') or '').strip()
            minutes = int(raw) if raw.isdigit() and int(raw) > 0 else 0
            if minutes:
                fixture.extra_time_seconds += minutes * 60
                fixture.save(update_fields=['extra_time_seconds', 'updated_at'])
                messages.success(request, f'Added {minutes} minute(s) of extra time.')
            else:
                messages.error(request, 'Enter a positive number of minutes.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'set_period':
            if not is_basketball:
                messages.error(request, 'Quarter navigation is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            direction = request.POST.get('direction')
            old_period = fixture.current_period or 1
            period = old_period
            if direction == 'next':
                period += 1
            elif direction == 'prev':
                period = max(1, period - 1)
            fixture.current_period = period
            update_fields = ['current_period', 'updated_at']
            if direction == 'next' and period != old_period and fixture.status == 'LIVE':
                # Advancing to a genuinely new quarter restarts the clocks and
                # TEAM fouls (bonus/penalty resets each quarter). Going back a
                # quarter (organizer correcting a misclick) does NOT reset
                # anything — the clock/fouls only ever restart when moving
                # forward into a quarter not yet played. Score is a
                # whole-game running total and each player's PERSONAL foul
                # count is cumulative for the whole game (foul-out tracking),
                # so neither is touched here.
                # Paused, not running — same reasoning as reset_quarter_clock:
                # a fresh quarter never auto-ticks until the organizer taps
                # Play.
                now = timezone.now()
                fixture.live_started_at = now
                fixture.extra_time_seconds = 0
                fixture.clock_paused_at = now
                fixture.shot_clock_seconds_remaining = fixture.shot_clock_duration_seconds
                fixture.shot_clock_running_since = None
                update_fields += ['live_started_at', 'extra_time_seconds', 'clock_paused_at',
                                  'shot_clock_seconds_remaining', 'shot_clock_running_since']
                fixture.participants.update(fouls=0)
            fixture.save(update_fields=update_fields)
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'foul':
            if not is_basketball:
                messages.error(request, 'Foul tracking is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            participant = get_object_or_404(FixtureParticipant, id=request.POST.get('participant_id'),
                                            fixture=fixture)
            delta = request.POST.get('delta')
            membership_id = request.POST.get('membership_id')
            with transaction.atomic():
                if delta == 'inc':
                    participant.fouls = (participant.fouls or 0) + 1
                    participant.save(update_fields=['fouls'])
                    # A foul record is always kept (team-level "no player"
                    # fouls included) so the "-" button below can pop the
                    # true most-recent foul off the stack, not just the most
                    # recent *attributed* one.
                    membership = None
                    if membership_id:
                        membership = get_object_or_404(TeamMembership, id=membership_id,
                                                       team_id=participant.team_id)
                    ScoreEvent.objects.create(
                        fixture=fixture, participant=participant, event_type='foul',
                        description=(f'Foul — {membership.name} (#{membership.jersey_number or "-"})'
                                     if membership else f'Foul — {participant.name} (team)'),
                        score_snapshot={'membership_id': membership.id if membership else None,
                                        'player_name': membership.name if membership else '',
                                        'jersey_number': membership.jersey_number if membership else '',
                                        'team_participant_id': participant.id},
                        created_by=request.user)
                elif delta == 'dec' and (participant.fouls or 0) > 0:
                    participant.fouls -= 1
                    participant.save(update_fields=['fouls'])
                    # LIFO: drop the most recently recorded foul for this
                    # team, whether or not it was attributed to a player.
                    last_foul = fixture.events.filter(
                        participant=participant, event_type='foul').order_by('-id').first()
                    if last_foul:
                        last_foul.delete()
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'set_quarter_length':
            if not is_basketball:
                messages.error(request, 'Quarter length is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            raw = (request.POST.get('minutes') or '').strip()
            minutes = int(raw) if raw.isdigit() and 1 <= int(raw) <= 60 else 0
            if minutes:
                fixture.quarter_length_seconds = minutes * 60
                fixture.save(update_fields=['quarter_length_seconds', 'updated_at'])
                messages.success(request, f'Quarter length set to {minutes} min.')
            else:
                messages.error(request, 'Enter a quarter length between 1 and 60 minutes.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'shot_clock_reset':
            # The shot clock is no longer independently started/paused by the
            # organizer (it auto-syncs to the quarter clock — see pause_clock/
            # resume_clock/reset_quarter_clock below). This is a manual
            # override to force the current cycle back to the full duration
            # without touching the quarter clock, e.g. after a possession
            # change — it keeps running if the quarter clock is running.
            if not is_basketball:
                messages.error(request, 'The shot clock is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            fixture.shot_clock_seconds_remaining = fixture.shot_clock_duration_seconds
            fixture.shot_clock_running_since = (
                timezone.now() if fixture.status == 'LIVE' and not fixture.clock_paused_at else None)
            fixture.save(update_fields=['shot_clock_seconds_remaining', 'shot_clock_running_since',
                                        'updated_at'])
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'set_shot_clock_duration':
            if not is_basketball:
                messages.error(request, 'The shot clock is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            raw = (request.POST.get('seconds') or '').strip()
            seconds = int(raw) if raw.isdigit() and 1 <= int(raw) <= 60 else 0
            if seconds:
                # Changing the limit immediately resets the current cycle to
                # the new duration, and every future auto-reset uses it too.
                fixture.shot_clock_duration_seconds = seconds
                fixture.shot_clock_seconds_remaining = seconds
                fixture.shot_clock_running_since = (
                    timezone.now() if fixture.status == 'LIVE' and not fixture.clock_paused_at else None)
                fixture.save(update_fields=['shot_clock_duration_seconds', 'shot_clock_seconds_remaining',
                                            'shot_clock_running_since', 'updated_at'])
                messages.success(request, f'Shot clock set to {seconds}s.')
            else:
                messages.error(request, 'Enter a shot clock duration between 1 and 60 seconds.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'pause_clock':
            if not is_basketball:
                messages.error(request, 'The match clock is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            if fixture.status == 'LIVE' and fixture.live_started_at and not fixture.clock_paused_at:
                now = timezone.now()
                fixture.clock_paused_at = now
                update_fields = ['clock_paused_at', 'updated_at']
                # Shot clock auto-syncs to the match clock: pausing the match
                # clock freezes the shot clock's current cycle position too.
                if fixture.shot_clock_running_since:
                    fixture.shot_clock_seconds_remaining = fixture.shot_clock_remaining_at(now)
                    fixture.shot_clock_running_since = None
                    update_fields += ['shot_clock_seconds_remaining', 'shot_clock_running_since']
                fixture.save(update_fields=update_fields)
                messages.info(request, 'Match clock paused.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'resume_clock':
            if not is_basketball:
                messages.error(request, 'The match clock is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            if fixture.clock_paused_at and fixture.live_started_at:
                now = timezone.now()
                fixture.live_started_at += now - fixture.clock_paused_at
                fixture.clock_paused_at = None
                # Shot clock auto-syncs to the match clock: resuming picks the
                # shot clock back up from wherever it was frozen.
                fixture.shot_clock_running_since = now
                fixture.save(update_fields=['live_started_at', 'clock_paused_at',
                                            'shot_clock_running_since', 'updated_at'])
                messages.info(request, 'Match clock resumed.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'reset_quarter_clock':
            if not is_basketball:
                messages.error(request, 'The match clock is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            if fixture.status == 'LIVE':
                # Resets the clock to the full quarter length, paused — the
                # organizer taps Play to actually start it counting down,
                # same as a fresh match start or a new quarter. The shot
                # clock auto-syncs: it resets to its full duration, paused,
                # right alongside the quarter clock.
                now = timezone.now()
                fixture.live_started_at = now
                fixture.extra_time_seconds = 0
                fixture.clock_paused_at = now
                fixture.shot_clock_seconds_remaining = fixture.shot_clock_duration_seconds
                fixture.shot_clock_running_since = None
                fixture.save(update_fields=['live_started_at', 'extra_time_seconds', 'clock_paused_at',
                                            'shot_clock_seconds_remaining', 'shot_clock_running_since',
                                            'updated_at'])
                messages.success(request, 'Quarter timer reset.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'adjust_clock_seconds':
            if not is_basketball:
                messages.error(request, 'The match clock is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            if fixture.status == 'LIVE' and fixture.live_started_at:
                from datetime import timedelta
                try:
                    delta = int(request.POST.get('seconds', '0'))
                except (TypeError, ValueError):
                    delta = 0
                if delta:
                    now = timezone.now()
                    if fixture.clock_paused_at:
                        current_remaining = fixture.paused_quarter_remaining_seconds or 0
                    else:
                        elapsed = (now - fixture.live_started_at).total_seconds()
                        current_remaining = (fixture.quarter_length_seconds
                                              + fixture.extra_time_seconds - elapsed)
                    # Clamp so remaining can't go negative; a shift larger than
                    # what's left just lands exactly on 0 instead of overshooting.
                    applied = max(delta, -current_remaining) if delta < 0 else delta
                    fixture.live_started_at += timedelta(seconds=applied)
                    fixture.save(update_fields=['live_started_at', 'updated_at'])
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'racket_point':
            if not is_racket:
                messages.error(request, 'Point scoring is only available for badminton/pickleball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            if fixture.status != 'LIVE' or not fixture.set_scores:
                messages.error(request, 'Start the match before recording points.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            side = request.POST.get('side')
            if side not in ('a', 'b'):
                messages.error(request, 'Unknown side.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            delta = 1 if request.POST.get('delta') != '-1' else -1
            sets = list(fixture.set_scores)
            current = dict(sets[-1])
            current[side] = max(0, current.get(side, 0) + delta)
            sets[-1] = current
            fixture.set_scores = sets
            fixture.save(update_fields=['set_scores', 'updated_at'])
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action in ('edit_set', 'delete_set'):
            if not is_racket:
                messages.error(request, 'Set controls are only available for badminton/pickleball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            # Correcting/removing a set is only offered while the match is
            # still LIVE — once it's COMPLETED, the winner has already been
            # recorded and (for a bracket/pool) may have advanced into a
            # later fixture, which nothing here can safely unwind.
            if fixture.status != 'LIVE' or not fixture.set_scores:
                messages.error(request, 'Sets can only be edited while the match is live.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

            idx_raw = request.POST.get('set_index')
            if not (idx_raw or '').isdigit() or int(idx_raw) >= len(fixture.set_scores):
                messages.error(request, 'Unknown set.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            idx = int(idx_raw)
            sets = list(fixture.set_scores)

            if action == 'delete_set':
                if len(sets) <= 1:
                    messages.error(request, 'A match needs at least one set.')
                    return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
                del sets[idx]
                fixture.set_scores = sets
                fixture.save(update_fields=['set_scores', 'updated_at'])
                messages.success(request, f'Set {idx + 1} deleted.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

            # edit_set
            a_raw, b_raw = request.POST.get('score_a'), request.POST.get('score_b')
            if not (a_raw or '').isdigit() or not (b_raw or '').isdigit():
                messages.error(request, 'Enter a valid score for both sides.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            sets[idx] = {'a': int(a_raw), 'b': int(b_raw)}
            fixture.set_scores = sets
            fixture.save(update_fields=['set_scores', 'updated_at'])
            messages.success(request, f'Set {idx + 1} updated.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action in ('finish_set', 'add_set'):
            if not is_racket:
                messages.error(request, 'Set controls are only available for badminton/pickleball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            if fixture.status != 'LIVE' or not fixture.set_scores:
                messages.error(request, 'Start the match before managing sets.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

            if action == 'add_set':
                sets = list(fixture.set_scores) + [{'a': 0, 'b': 0}]
                fixture.set_scores = sets
                fixture.save(update_fields=['set_scores', 'updated_at'])
                messages.success(request, 'Extra set added.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

            # finish_set: freeze the live set, then either auto-complete the
            # match (a side has reached sets_to_win) or open the next set.
            finished_a = sum(1 for s in fixture.set_scores if s.get('a', 0) > s.get('b', 0))
            finished_b = sum(1 for s in fixture.set_scores if s.get('b', 0) > s.get('a', 0))
            if len(participants) == 2 and (finished_a >= fixture.sets_to_win
                                            or finished_b >= fixture.sets_to_win):
                # Score = sets won, not raw points — the generic engine picks
                # the winner off the higher `score`, and a "2-1 in sets"
                # scoreline is what every other view should show for this
                # match; the full set-by-set points live in set_scores.
                data = {'finalize': True,
                        str(participants[0].id): {'score': finished_a},
                        str(participants[1].id): {'score': finished_b}}
                apply_result(fixture, data, actor=request.user)
                messages.success(request, f'Match finalized {finished_a}-{finished_b} in sets.')
            else:
                sets = list(fixture.set_scores) + [{'a': 0, 'b': 0}]
                fixture.set_scores = sets
                fixture.save(update_fields=['set_scores', 'updated_at'])
                messages.success(request, 'Set finished.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action in ('chess_win', 'chess_draw'):
            # Chess has no separate score page — these actions are triggered
            # from the fixture tile on the fixtures list itself (see
            # _fixture_row.html's Save Result/Draw Result popups), so they
            # land back there rather than on score_fixture.
            if not is_chess:
                messages.error(request, 'This action is only available for chess matches.')
                return redirect('fixtures_manage', slug=slug)
            if len(participants) != 2:
                messages.error(request, 'This game needs exactly two players before a result can be saved.')
                return redirect('fixtures_manage', slug=slug)
            p0, p1 = participants
            if action == 'chess_draw':
                data = {'finalize': True, str(p0.id): {'score': 0.5}, str(p1.id): {'score': 0.5}}
                apply_result(fixture, data, actor=request.user)
                messages.success(request, 'Draw recorded — 0.5-0.5.')
            else:
                winner_id = request.POST.get('winner_id')
                if winner_id == str(p0.id):
                    winner, loser = p0, p1
                elif winner_id == str(p1.id):
                    winner, loser = p1, p0
                else:
                    messages.error(request, 'Choose which player won.')
                    return redirect('fixtures_manage', slug=slug)
                data = {'finalize': True, str(winner.id): {'score': 1}, str(loser.id): {'score': 0}}
                apply_result(fixture, data, actor=request.user)
                messages.success(request, f'{winner.name} wins 1-0.')
            return redirect('fixtures_manage', slug=slug)

        if action == 'chess_swap_colors':
            if not is_chess:
                messages.error(request, 'This action is only available for chess matches.')
                return redirect('fixtures_manage', slug=slug)
            if len(participants) != 2:
                messages.error(request, 'This game needs exactly two players.')
                return redirect('fixtures_manage', slug=slug)
            # Swapping only changes which FixtureParticipant displays as
            # White/Black (their score/is_winner stay attached to the same
            # row) — safe to allow anytime, including after a result is
            # already recorded, so a mistake can be corrected.
            black_id = request.POST.get('black_id')
            # ordered_participants() sorts by slot ascending, so p0 is always
            # currently White (slot 0) and p1 always currently Black (slot 1)
            # — swap only if the organizer picked the current White to be Black.
            p0, p1 = participants
            if black_id not in (str(p0.id), str(p1.id)):
                messages.error(request, 'Choose which player is Black.')
                return redirect('fixtures_manage', slug=slug)
            if black_id == str(p0.id):
                with transaction.atomic():
                    p0.slot, p1.slot = 1, 0
                    p0.save(update_fields=['slot'])
                    p1.save(update_fields=['slot'])
            messages.success(request, 'Colors updated.')
            return redirect('fixtures_manage', slug=slug)

        if action == 'adjust_score':
            if not is_basketball:
                messages.error(request, 'Score adjustment is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            if fixture.status != 'LIVE':
                messages.error(request, 'Start the match before adjusting the score.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            participant = get_object_or_404(FixtureParticipant, id=request.POST.get('participant_id'),
                                            fixture=fixture)
            participant.score = max(0, (participant.score or 0) - 1)
            participant.save(update_fields=['score'])
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'score_point':
            if not is_basketball:
                messages.error(request, 'Point scoring is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            if fixture.status != 'LIVE':
                messages.error(request, 'Start the match before recording points.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            points_raw = request.POST.get('points')
            if points_raw not in _BASKETBALL_POINT_VALUES:
                messages.error(request, 'Choose +1, +2, or +3.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            participant = get_object_or_404(FixtureParticipant, id=request.POST.get('participant_id'),
                                            fixture=fixture)
            points = int(points_raw)
            if fixture.individual_scoring_enabled:
                membership_id = request.POST.get('membership_id')
                if not membership_id:
                    messages.error(request, 'Select the scoring player before recording a point.')
                    return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
                # Scoped to `participant`'s own team — a player from the other
                # side simply cannot resolve here, whatever the client sent.
                membership = get_object_or_404(TeamMembership, id=membership_id, team_id=participant.team_id)
                with transaction.atomic():
                    participant.score = (participant.score or 0) + points
                    participant.save(update_fields=['score'])
                    ScoreEvent.objects.create(
                        fixture=fixture, participant=participant, event_type='score',
                        description=f'{participant.name} +{points} — {membership.name} '
                                    f'(#{membership.jersey_number or "-"})',
                        score_snapshot={'membership_id': membership.id, 'player_name': membership.name,
                                        'jersey_number': membership.jersey_number, 'points': points,
                                        'team_participant_id': participant.id},
                        created_by=request.user)
                messages.success(request, f'+{points} — {membership.name} (#{membership.jersey_number or "-"}).')
            else:
                # Individual scoring is off for this match: apply straight to
                # the team, no player attribution, no roster lookup.
                with transaction.atomic():
                    participant.score = (participant.score or 0) + points
                    participant.save(update_fields=['score'])
                    ScoreEvent.objects.create(
                        fixture=fixture, participant=participant, event_type='score',
                        description=f'{participant.name} +{points}',
                        score_snapshot={'membership_id': None, 'player_name': '', 'jersey_number': '',
                                        'points': points, 'team_participant_id': participant.id},
                        created_by=request.user)
                messages.success(request, f'{participant.name} +{points}.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        if action == 'undo_last_score':
            if not is_basketball:
                messages.error(request, 'Undo is only available for basketball matches.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            if fixture.status != 'LIVE':
                messages.error(request, 'Start the match before undoing an action.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            # Most-recent-first ordering by primary key (not created_at,
            # which can tie within the same second) gives a reliable LIFO
            # undo stack over the score events already persisted per point.
            last_event = fixture.events.filter(event_type='score').order_by('-id').first()
            if not last_event:
                messages.error(request, 'Nothing to undo.')
                return redirect('score_fixture', slug=slug, fixture_id=fixture_id)
            with transaction.atomic():
                snap = last_event.score_snapshot or {}
                points = int(snap.get('points') or 0)
                participant = last_event.participant
                if participant is not None:
                    participant.score = max(0, (participant.score or 0) - points)
                    participant.save(update_fields=['score'])
                player_name = snap.get('player_name') or 'that player'
                last_event.delete()
            messages.success(request, f'Undid +{points} — {player_name}.')
            return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

        # --- parse a score submission per format ---
        data = {'finalize': action == 'finalize'}
        fmt = t.format
        is_lobby = (fmt == C.FORMAT_ROUND_ROBIN and t.sport.slug == 'mobile-esports')
        for p in participants:
            key = str(p.id)
            if fmt in (C.FORMAT_TIME_TRIAL, C.FORMAT_SINGLE_EVENT):
                data[key] = {'time_ms': parse_time_to_ms(request.POST.get(f'time_{p.id}', '')),
                             'result_state': request.POST.get(f'state_{p.id}', 'OK')}
            elif is_lobby:
                data[key] = {'kills': request.POST.get(f'kills_{p.id}') or 0,
                             'placement': request.POST.get(f'place_{p.id}') or ''}
            else:
                raw = request.POST.get(f'score_{p.id}', '')
                data[key] = {'score': raw if raw != '' else None}
        apply_result(fixture, data, actor=request.user)
        messages.success(request, 'Result saved.' + (' Match finalized.' if data['finalize'] else ''))
        return redirect('score_fixture', slug=slug, fixture_id=fixture_id)

    ctx = {
        'tournament': t, 'fixture': fixture, 'participants': participants,
        'is_lobby': (t.format == C.FORMAT_ROUND_ROBIN and t.sport.slug == 'mobile-esports'),
        'is_time': t.format in (C.FORMAT_TIME_TRIAL, C.FORMAT_SINGLE_EVENT),
        'is_basketball': is_basketball,
        'is_racket': is_racket,
        'events': fixture.events.exclude(event_type='score')[:30],
        'format_ms': format_ms,
    }
    if is_basketball:
        player_choices, individual_rows, individual_rows_by_team = _basketball_scoring_context(fixture)
        ctx['player_choices'] = player_choices
        ctx['individual_rows'] = individual_rows
        ctx['individual_rows_by_team'] = individual_rows_by_team
        ctx['can_undo_score'] = fixture.events.filter(event_type='score').exists()
    return render(request, 'organizer/score.html', ctx)


# ======================================================================
# Highlights
# ======================================================================
@approved_organizer_required
def highlight_manage(request, slug, fixture_id):
    t = _owned(request, slug)
    fixture = get_object_or_404(Fixture, id=fixture_id, tournament=t)
    instance = fixture.highlights.first()
    form = HighlightForm(request.POST or None, request.FILES or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        h = form.save(commit=False)
        h.tournament = t
        h.fixture = fixture
        h.created_by = request.user
        if request.POST.get('publish'):
            h.published_at = h.published_at or timezone.now()
        h.save()
        messages.success(request, 'Highlights saved.')
        return redirect('match_detail', slug=t.slug, pk=fixture.id)
    return render(request, 'organizer/highlight.html',
                  {'tournament': t, 'fixture': fixture, 'form': form})


# ======================================================================
# Player participation
# ======================================================================
@player_required
def tournament_join(request, slug):
    t = get_object_or_404(Tournament.objects.public(), slug=slug)
    profile, _ = PlayerProfile.objects.get_or_create(user=request.user)

    if t.status in ('COMPLETED', 'CANCELLED'):
        messages.error(request, 'This tournament is no longer accepting entries.')
        return redirect('tournament_detail', slug=slug)
    if t.registration_deadline and timezone.now() > t.registration_deadline:
        messages.error(request, 'Registration has closed.')
        return redirect('tournament_detail', slug=slug)

    if request.method == 'POST':
        if t.is_team_based:
            team = get_object_or_404(Team, id=request.POST.get('team_id'), entries__tournament=t)
            if TeamMembership.objects.filter(team=team, player=profile).exists():
                messages.info(request, 'You have already requested to join this team.')
            else:
                TeamMembership.objects.create(team=team, player=profile, is_approved=False)
                Notification.push(
                    t.organizer.user,
                    f'{profile.user.display_name} requested to join {team.name}.',
                    url=f'/organizer/t/{t.slug}/', verb='join')
                messages.success(request, 'Join request sent to the organizer.')
        else:
            if t.max_participants and t.participant_count() >= t.max_participants:
                messages.error(request, 'This tournament is full.')
                return redirect('tournament_detail', slug=slug)
            _, created = IndividualRegistration.objects.get_or_create(
                tournament=t, player=profile,
                defaults={'display_name': profile.user.display_name, 'status': 'APPROVED'})
            if created:
                Notification.push(t.organizer.user,
                                  f'{profile.user.display_name} registered for {t.name}.',
                                  url=f'/organizer/t/{t.slug}/participants', verb='registration')
            messages.success(request, 'You are registered!' if created else 'Already registered.')
        return redirect('tournament_detail', slug=slug)

    teams = t.team_entries.select_related('team').filter(status='APPROVED') if t.is_team_based else None
    return render(request, 'players/join.html', {'tournament': t, 'teams': teams})


# ======================================================================
# Following a tournament
# ======================================================================
@player_required
@require_POST
def tournament_follow(request, slug):
    """Follow / unfollow. Followers are the audience for result notifications —
    the one channel that reaches someone who is neither playing nor organizing."""
    t = get_object_or_404(Tournament.objects.public(), slug=slug)
    existing = Follow.objects.filter(user=request.user, tournament=t).first()
    if existing:
        existing.delete()
        messages.info(request, f'You are no longer following {t.name}.')
    else:
        Follow.objects.create(user=request.user, tournament=t)
        messages.success(request, f'Following {t.name} — we will tell you when results land.')
    return redirect(request.META.get('HTTP_REFERER') or t.get_absolute_url())


@player_required
def my_following(request):
    tournaments = Tournament.objects.filter(
        followers__user=request.user).select_related('sport', 'organizer__user').distinct()
    return render(request, 'players/following.html', {'tournaments': tournaments})


# ======================================================================
# Live-score JSON API (public, polled)
# ======================================================================
def fixture_live_json(request, fixture_id):
    fixture = get_object_or_404(
        Fixture.objects.select_related('tournament__sport'), id=fixture_id, is_removed=False)
    # Never leak an unpublished or moderated-away tournament through the API.
    t = fixture.tournament
    if t.status == 'DRAFT' or t.is_removed:
        raise PermissionDenied()

    participants = list(fixture.ordered_participants())
    parts = [{
        'id': p.id, 'name': p.name, 'initials': p.initials,
        'score': float(p.score) if p.score is not None else None,
        'rank': p.rank, 'is_winner': p.is_winner,
        'state': p.result_state,
        'time': p.time_display or None,
        'bib': p.bib or None,
        'pace': p.pace_display or None,
        'kills': p.kills,
    } for p in participants]

    individual_scoring_html = None
    if t.sport.slug == 'basketball':
        # Same read-only partial the public match page renders on first load
        # (template/public/_individual_scoring.html), fed by the exact same
        # _basketball_scoring_context() the organizer's own Individual
        # Scoring panel uses — so a poll refresh can never show numbers that
        # disagree with the organizer page, and there's nothing here
        # re-deriving player totals a second way.
        _, _, individual_rows_by_team = _basketball_scoring_context(fixture)
        individual_scoring_html = render_to_string('public/_individual_scoring.html', {
            'fixture': fixture, 'participants': participants,
            'individual_rows_by_team': individual_rows_by_team,
        }, request=request)

    clock = None
    if t.sport.slug == 'basketball':
        # Same fields + the same server-computed paused_quarter_remaining_seconds
        # the organizer scoring page already renders — the public match page's
        # quarter-clock AND shot-clock elements (static/js/match-clock.js) read
        # these exact attributes, so this just keeps those two clocks in sync
        # with the organizer's, not a second/duplicate timer implementation.
        clock = {
            'started_at': fixture.live_started_at.isoformat() if fixture.live_started_at else None,
            'extra_seconds': fixture.extra_time_seconds,
            'quarter_length_seconds': fixture.quarter_length_seconds,
            'paused': bool(fixture.clock_paused_at),
            'paused_remaining_seconds': fixture.paused_quarter_remaining_seconds,
            'period_display': fixture.period_display,
            'shot_running': bool(fixture.shot_clock_running_since),
            'shot_started_at': (fixture.shot_clock_running_since.isoformat()
                                if fixture.shot_clock_running_since else None),
            'shot_remaining_seconds': fixture.shot_clock_seconds_remaining,
            'shot_duration_seconds': fixture.shot_clock_duration_seconds,
        }

    return JsonResponse({
        'status': fixture.status,
        'round': fixture.round_name,
        'participants': parts,
        # Drives the win-probability bar on the live scoreboard.
        'win_probability': fixture.win_probability,
        'clock': clock,
        'individual_scoring_html': individual_scoring_html,
        'events': [{'text': e.description, 'at': e.created_at.strftime('%H:%M')}
                   for e in fixture.events.all()[:15]],
        'updated': timezone.now().isoformat(),
    })
