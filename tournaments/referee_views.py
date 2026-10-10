import json
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import models
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_POST

from accounts.models import RefereeProfile
from .models import (
    Fixture,
    FixtureRefereeAssignment,
    Tournament,
    TournamentRefereeRegistration,
)
from .views import _owned


@require_GET
def tournament_referees_search_api(request, slug):
    """AJAX search endpoint to find registered referees by username or name to add to tournament pool."""
    t = _owned(request, slug)
    q = request.GET.get('q', '').strip()
    if not q or len(q) < 1:
        return JsonResponse({'results': []})

    User = get_user_model()
    # Exclude users already active in this tournament's pool
    existing_pool_user_ids = t.registered_referees.filter(is_active=True).values_list('user_id', flat=True)

    qs = User.objects.filter(referee_profile__isnull=False).exclude(id__in=existing_pool_user_ids)
    qs = qs.filter(
        models.Q(username__icontains=q) |
        models.Q(first_name__icontains=q) |
        models.Q(last_name__icontains=q)
    ).select_related('referee_profile')[:10]

    results = []
    for u in qs:
        rp = u.referee_profile
        results.append({
            'id': u.id,
            'username': u.username,
            'display_name': u.display_name,
            'experience_level': rp.get_experience_level_display(),
            'years_experience': rp.years_experience,
            'is_verified': rp.is_verified,
            'is_available': rp.is_available,
            'preferred_location': rp.preferred_location,
        })
    return JsonResponse({'results': results})


@require_POST
def tournament_referee_add(request, slug):
    """Add an existing referee into the tournament's official referee pool."""
    t = _owned(request, slug)
    user_id = request.POST.get('user_id')
    username = request.POST.get('username', '').strip()

    User = get_user_model()
    user = None
    if user_id:
        user = get_object_or_404(User, id=user_id)
    elif username:
        user = User.objects.filter(username__iexact=username).first()
        if not user:
            messages.error(request, f"User '@{username}' was not found.")
            return redirect('fixtures_manage', slug=slug)
    else:
        messages.error(request, "Please provide a valid referee username or ID.")
        return redirect('fixtures_manage', slug=slug)

    if not hasattr(user, 'referee_profile'):
        messages.error(request, f"User '@{user.username}' has not registered as a referee yet.")
        return redirect('fixtures_manage', slug=slug)

    reg, created = TournamentRefereeRegistration.objects.update_or_create(
        tournament=t,
        user=user,
        defaults={'is_active': True, 'registered_by': request.user}
    )
    messages.success(request, f"Referee @{user.username} is now registered in the {t.name} officiating pool.")
    return redirect('fixtures_manage', slug=slug)


@require_POST
def tournament_referee_remove(request, slug, user_id):
    """Remove a referee from the tournament pool."""
    t = _owned(request, slug)
    reg = get_object_or_404(TournamentRefereeRegistration, tournament=t, user_id=user_id)
    reg.is_active = False
    reg.save(update_fields=['is_active'])
    messages.success(request, f"Referee @{reg.user.username} was removed from the tournament pool.")
    return redirect('fixtures_manage', slug=slug)


@require_POST
def tournament_referees_auto_assign(request, slug):
    """Option A: Balanced distribution of tournament pool referees across fixtures."""
    t = _owned(request, slug)
    overwrite = bool(request.POST.get('overwrite_existing'))
    role = request.POST.get('role', 'PRIMARY')
    if role not in ('PRIMARY', 'ASSISTANT', 'TABLE'):
        role = 'PRIMARY'

    pool_regs = list(t.registered_referees.filter(is_active=True).select_related('user'))
    if not pool_regs:
        messages.error(request, "No referees found in the tournament pool. Please add referees to the pool first.")
        return redirect('fixtures_manage', slug=slug)

    pool_users = [r.user for r in pool_regs]
    fixtures_qs = t.fixtures.filter(is_removed=False).exclude(status__in=['COMPLETED', 'CANCELLED']).order_by('scheduled_time', 'id')

    if not fixtures_qs.exists():
        messages.warning(request, "No upcoming or scheduled fixtures found to assign.")
        return redirect('fixtures_manage', slug=slug)

    if overwrite:
        # Clear existing uncompleted assignments
        FixtureRefereeAssignment.objects.filter(fixture__in=fixtures_qs).delete()
        fixtures_to_assign = list(fixtures_qs)
    else:
        # Preserve manual/existing assignments: exclude fixtures that already have an assignment for this role
        already_assigned_fx_ids = FixtureRefereeAssignment.objects.filter(
            fixture__in=fixtures_qs, role=role
        ).values_list('fixture_id', flat=True)
        fixtures_to_assign = list(fixtures_qs.exclude(id__in=already_assigned_fx_ids))

    if not fixtures_to_assign:
        messages.info(request, "All fixtures already have assigned officials. Check 'Overwrite existing assignments' if you wish to redistribute.")
        return redirect('fixtures_manage', slug=slug)

    # Balanced round-robin distribution
    assigned_count = 0
    num_refs = len(pool_users)
    for idx, fx in enumerate(fixtures_to_assign):
        ref_user = pool_users[idx % num_refs]
        FixtureRefereeAssignment.objects.update_or_create(
            fixture=fx,
            user=ref_user,
            defaults={'role': role}
        )
        assigned_count += 1

    messages.success(request, f"Successfully auto-assigned {assigned_count} fixture(s) evenly among {num_refs} referee(s).")
    return redirect('fixtures_manage', slug=slug)


@require_POST
def fixture_referee_assign(request, slug, fixture_id):
    """Option B: Manually assign a referee to a specific fixture with a role."""
    t = _owned(request, slug)
    fixture = get_object_or_404(Fixture, id=fixture_id, tournament=t)
    user_id = request.POST.get('user_id')
    role = request.POST.get('role', 'PRIMARY')
    notes = request.POST.get('notes', '').strip()

    User = get_user_model()
    user = get_object_or_404(User, id=user_id)
    if not hasattr(user, 'referee_profile'):
        messages.error(request, f"User '@{user.username}' is not a registered referee.")
        return redirect('fixtures_manage', slug=slug)

    # Ensure user is in the tournament pool
    TournamentRefereeRegistration.objects.get_or_create(
        tournament=t, user=user,
        defaults={'is_active': True, 'registered_by': request.user}
    )

    FixtureRefereeAssignment.objects.update_or_create(
        fixture=fixture,
        user=user,
        defaults={'role': role, 'notes': notes}
    )
    messages.success(request, f"Assigned @{user.username} as {role.title()} Official for this match.")
    return redirect('fixtures_manage', slug=slug)


@require_POST
def fixture_referee_unassign(request, slug, fixture_id, user_id):
    """Manually unassign a referee from a specific fixture."""
    t = _owned(request, slug)
    fixture = get_object_or_404(Fixture, id=fixture_id, tournament=t)
    FixtureRefereeAssignment.objects.filter(fixture=fixture, user_id=user_id).delete()
    messages.success(request, "Referee unassigned from fixture.")
    return redirect('fixtures_manage', slug=slug)

