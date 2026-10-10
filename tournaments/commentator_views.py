from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import models
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST, require_GET

from accounts.decorators import approved_organizer_required
from accounts.models import CommentatorProfile
from .models import (
    Fixture,
    FixtureCommentatorAssignment,
    Tournament,
    TournamentCommentatorRegistration,
)
from .views import _owned

User = get_user_model()


@approved_organizer_required
@require_GET
def tournament_commentators_search_api(request, slug):
    """AJAX endpoint for searching available commentators by username, name, or sport."""
    t = _owned(request, slug)
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})

    commentators = CommentatorProfile.objects.filter(
        models.Q(user__username__icontains=query) |
        models.Q(user__first_name__icontains=query) |
        models.Q(languages__icontains=query) |
        models.Q(sports__name__icontains=query)
    ).select_related('user').prefetch_related('sports').distinct()[:15]

    already_registered_ids = set(
        TournamentCommentatorRegistration.objects.filter(
            tournament=t, is_active=True
        ).values_list('user_id', flat=True)
    )

    results = []
    for c in commentators:
        results.append({
            'user_id': c.user.id,
            'username': c.user.username,
            'name': c.user.display_name,
            'experience': c.get_experience_level_display(),
            'languages': c.languages,
            'is_available': c.is_available,
            'is_already_added': c.user.id in already_registered_ids,
        })

    return JsonResponse({'results': results})


@approved_organizer_required
@require_POST
def tournament_commentator_add(request, slug):
    """Add a qualified commentator to the tournament's official pool."""
    t = _owned(request, slug)
    user_id = request.POST.get('user_id')
    username = request.POST.get('username', '').strip()

    target_user = None
    if user_id:
        target_user = get_object_or_404(User, id=user_id)
    elif username:
        target_user = get_object_or_404(User, username=username)

    if not target_user:
        messages.error(request, 'Please specify a valid commentator user.')
        return redirect('fixtures_manage', slug=slug)

    reg, created = TournamentCommentatorRegistration.objects.get_or_create(
        tournament=t,
        user=target_user,
        defaults={'registered_by': request.user, 'is_active': True}
    )
    if not created and not reg.is_active:
        reg.is_active = True
        reg.registered_by = request.user
        reg.save(update_fields=['is_active', 'registered_by', 'updated_at'])

    messages.success(request, f'Commentator @{target_user.username} added to tournament pool.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def tournament_commentator_remove(request, slug, user_id=None):
    """Deactivate a commentator from the tournament's official pool."""
    t = _owned(request, slug)
    user_id = user_id or request.POST.get('user_id')
    reg = get_object_or_404(TournamentCommentatorRegistration, tournament=t, user_id=user_id)
    reg.is_active = False
    reg.save(update_fields=['is_active', 'updated_at'])

    # Also remove any future unplayed fixture assignments
    FixtureCommentatorAssignment.objects.filter(
        fixture__tournament=t,
        user_id=user_id,
        fixture__status__in=['SCHEDULED']
    ).delete()

    messages.success(request, f'Commentator @{reg.user.username} removed from tournament pool.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def fixture_commentator_assign(request, slug, fixture_id):
    """Manually assign a commentator to a specific fixture with role and notes."""
    t = _owned(request, slug)
    fixture = get_object_or_404(Fixture, id=fixture_id, tournament=t)
    user_id = request.POST.get('user_id')
    role = request.POST.get('role', 'LEAD')
    notes = request.POST.get('notes', '').strip()

    user = get_object_or_404(User, id=user_id)

    # Ensure registered in tournament pool
    TournamentCommentatorRegistration.objects.get_or_create(
        tournament=t, user=user, defaults={'registered_by': request.user, 'is_active': True}
    )

    assign, created = FixtureCommentatorAssignment.objects.get_or_create(
        fixture=fixture,
        user=user,
        defaults={'role': role, 'notes': notes}
    )
    if not created:
        assign.role = role
        assign.notes = notes
        assign.save(update_fields=['role', 'notes'])

    messages.success(request, f'Commentator @{user.username} assigned to match {fixture.match_id}.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def fixture_commentator_unassign(request, slug, fixture_id, user_id):
    """Remove a commentator assignment from a specific fixture."""
    t = _owned(request, slug)
    fixture = get_object_or_404(Fixture, id=fixture_id, tournament=t)
    FixtureCommentatorAssignment.objects.filter(fixture=fixture, user_id=user_id).delete()
    messages.success(request, 'Commentator assignment removed.')
    return redirect('fixtures_manage', slug=slug)


@approved_organizer_required
@require_POST
def tournament_commentators_auto_assign(request, slug):
    """Auto-assign pool commentators evenly across fixtures."""
    t = _owned(request, slug)
    pool = list(TournamentCommentatorRegistration.objects.filter(
        tournament=t, is_active=True
    ).select_related('user'))

    if not pool:
        messages.error(request, 'No active commentators in tournament pool to assign.')
        return redirect('fixtures_manage', slug=slug)

    fixtures = list(t.fixtures.filter(is_removed=False).exclude(status__in=['COMPLETED', 'CANCELLED']).order_by('scheduled_time', 'id'))
    if not fixtures:
        messages.warning(request, 'No active or upcoming fixtures available to assign commentators.')
        return redirect('fixtures_manage', slug=slug)

    role = request.POST.get('role', 'LEAD')
    overwrite = request.POST.get('overwrite_existing') == '1'
    assigned_count = 0

    for idx, fx in enumerate(fixtures):
        if not overwrite and fx.commentator_assignments.exists():
            continue

        selected_commentator = pool[idx % len(pool)]

        if overwrite:
            fx.commentator_assignments.all().delete()

        FixtureCommentatorAssignment.objects.create(
            fixture=fx,
            user=selected_commentator.user,
            role=role,
            notes='Auto-assigned caster'
        )
        assigned_count += 1

    messages.success(request, f'Auto-assigned commentators across {assigned_count} fixtures.')
    return redirect('fixtures_manage', slug=slug)
