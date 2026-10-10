import json
import time
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse, StreamingHttpResponse, HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET

from accounts.decorators import login_required_msg
from .models import (
    CommentaryEntry,
    Fixture,
    FixtureCommentatorAssignment,
    Tournament,
)
from .views import _owned


def _can_commentate(request, fixture, slug):
    """Check if the current authenticated user is an assigned commentator or tournament organizer."""
    if not request.user.is_authenticated:
        return False, False
    is_assigned = FixtureCommentatorAssignment.objects.filter(fixture=fixture, user=request.user).exists()
    is_org = False
    try:
        _owned(request, slug)
        is_org = True
    except Exception:
        is_org = False
    return (is_assigned or is_org), is_org


@login_required_msg
def fixture_commentary_workspace(request, slug, fixture_id):
    """Dedicated Live Commentary Workspace for assigned commentators and organizers."""
    fixture = get_object_or_404(
        Fixture.objects.select_related('tournament__sport', 'tournament__venue'),
        id=fixture_id, tournament__slug=slug, is_removed=False
    )
    can_cast, is_org = _can_commentate(request, fixture, slug)
    if not can_cast:
        messages.error(request, 'You are not assigned as a commentator for this fixture.')
        return redirect('commentator_dashboard')

    # Load recent commentary entries (chronological order)
    entries = fixture.commentary_entries.filter(is_deleted=False).select_related('author').order_by('created_at', 'id')
    participants = fixture.ordered_participants()
    my_assignment = fixture.commentator_assignments.filter(user=request.user).first()

    return render(request, 'tournaments/commentary_workspace.html', {
        'fixture': fixture,
        'tournament': fixture.tournament,
        'participants': participants,
        'entries': entries,
        'my_assignment': my_assignment,
        'is_org': is_org,
        'categories': CommentaryEntry.CATEGORY_CHOICES,
        'last_entry_id': entries.last().id if entries.exists() else 0,
    })


@login_required_msg
@require_POST
def commentary_publish_api(request, slug, fixture_id):
    """API to publish a new commentary entry for an authorized fixture."""
    fixture = get_object_or_404(
        Fixture.objects.select_related('tournament__sport'),
        id=fixture_id, tournament__slug=slug, is_removed=False
    )
    can_cast, _ = _can_commentate(request, fixture, slug)
    if not can_cast:
        return JsonResponse({'error': 'You do not have permission to commentate on this match.'}, status=403)

    text = (request.POST.get('text') or '').strip()
    if not text:
        return JsonResponse({'error': 'Commentary text cannot be empty.'}, status=400)
    if len(text) > 1500:
        return JsonResponse({'error': 'Commentary text exceeds 1,500 characters.'}, status=400)

    recent_duplicate = fixture.commentary_entries.filter(
        author=request.user,
        text=text,
        is_deleted=False,
        created_at__gte=timezone.now() - timezone.timedelta(seconds=15)
    ).exists()
    if recent_duplicate:
        return JsonResponse({'error': 'Duplicate commentary detected. Please wait before submitting the same update.'}, status=400)

    category = (request.POST.get('category') or CommentaryEntry.CATEGORY_GENERAL).upper().strip()
    valid_categories = dict(CommentaryEntry.CATEGORY_CHOICES)
    if category not in valid_categories:
        category = CommentaryEntry.CATEGORY_GENERAL

    # Capture current authoritative score snapshot and match clock
    scores_snap = {
        str(p.id): (float(p.score) if p.score is not None else None)
        for p in fixture.participants.all()
    }
    custom_clock = (request.POST.get('match_clock') or '').strip()
    if custom_clock:
        match_clock = custom_clock
    else:
        match_clock = fixture.period_display or ''
        if fixture.status == 'LIVE' and fixture.quarter_length_seconds:
            rem = fixture.paused_quarter_remaining_seconds
            if rem is not None:
                mins, secs = divmod(rem, 60)
                match_clock += f" {mins:02d}:{secs:02d}"

    entry = CommentaryEntry.objects.create(
        fixture=fixture,
        author=request.user,
        text=text,
        category=category,
        score_snapshot=scores_snap,
        match_clock=match_clock,
    )

    return JsonResponse({
        'success': True,
        'entry': {
            'id': entry.id,
            'text': entry.text,
            'category': entry.category,
            'category_display': entry.get_category_display(),
            'author_id': entry.author_id,
            'author_name': request.user.display_name,
            'author_username': request.user.username,
            'created_at': entry.created_at.strftime('%H:%M'),
            'created_iso': entry.created_at.isoformat(),
            'match_clock': entry.match_clock,
            'score_snapshot': entry.score_snapshot,
            'can_edit': True,
        }
    })


@login_required_msg
@require_POST
def commentary_edit_api(request, slug, fixture_id, entry_id):
    """API for editing an existing commentary entry by its author or organizer."""
    fixture = get_object_or_404(Fixture, id=fixture_id, tournament__slug=slug, is_removed=False)
    entry = get_object_or_404(CommentaryEntry, id=entry_id, fixture=fixture, is_deleted=False)

    _, is_org = _can_commentate(request, fixture, slug)
    if entry.author_id != request.user.id and not is_org:
        return JsonResponse({'error': 'You cannot edit another commentator\'s entry.'}, status=403)

    text = (request.POST.get('text') or '').strip()
    if not text:
        return JsonResponse({'error': 'Commentary text cannot be empty.'}, status=400)
    if len(text) > 1500:
        return JsonResponse({'error': 'Commentary text exceeds 1,500 characters.'}, status=400)

    category = (request.POST.get('category') or entry.category).upper().strip()
    if category in dict(CommentaryEntry.CATEGORY_CHOICES):
        entry.category = category

    entry.text = text
    entry.edited_at = timezone.now()
    entry.save(update_fields=['text', 'category', 'edited_at'])

    return JsonResponse({
        'success': True,
        'entry': {
            'id': entry.id,
            'text': entry.text,
            'category': entry.category,
            'category_display': entry.get_category_display(),
            'edited_at': entry.edited_at.strftime('%H:%M'),
        }
    })


@login_required_msg
@require_POST
def commentary_delete_api(request, slug, fixture_id, entry_id):
    """Soft delete a commentary entry."""
    fixture = get_object_or_404(Fixture, id=fixture_id, tournament__slug=slug, is_removed=False)
    entry = get_object_or_404(CommentaryEntry, id=entry_id, fixture=fixture, is_deleted=False)

    _, is_org = _can_commentate(request, fixture, slug)
    if entry.author_id != request.user.id and not is_org:
        return JsonResponse({'error': 'You cannot delete another commentator\'s entry.'}, status=403)

    entry.is_deleted = True
    entry.save(update_fields=['is_deleted'])
    return JsonResponse({'success': True, 'deleted_id': entry.id})


@require_GET
def commentary_sync_api(request, slug, fixture_id):
    """Cursor-based synchronization returning latest entries, removals, and official score state."""
    fixture = get_object_or_404(
        Fixture.objects.select_related('tournament__sport'),
        id=fixture_id, tournament__slug=slug, is_removed=False
    )
    try:
        since_id = int(request.GET.get('since_id', 0) or 0)
    except (TypeError, ValueError):
        since_id = 0

    new_entries = list(fixture.commentary_entries.filter(
        id__gt=since_id, is_deleted=False
    ).select_related('author').order_by('id')[:60])

    deleted_entries = list(fixture.commentary_entries.filter(
        id__gt=since_id, is_deleted=True
    ).values_list('id', flat=True))

    entries_data = []
    max_id = since_id
    current_user_id = request.user.id if request.user.is_authenticated else None

    for e in new_entries:
        max_id = max(max_id, e.id)
        entries_data.append({
            'id': e.id,
            'text': e.text,
            'category': e.category,
            'category_display': e.get_category_display(),
            'author_id': e.author_id,
            'author_name': e.author.display_name if e.author else 'Commentator',
            'author_username': e.author.username if e.author else '',
            'created_at': e.created_at.strftime('%H:%M'),
            'created_iso': e.created_at.isoformat(),
            'match_clock': e.match_clock,
            'score_snapshot': e.score_snapshot,
            'edited_at': e.edited_at.strftime('%H:%M') if e.edited_at else None,
            'can_edit': bool(current_user_id and current_user_id == e.author_id),
        })

    from .views import build_fixture_live_payload
    live_payload = build_fixture_live_payload(fixture, request=request)

    return JsonResponse({
        'status': fixture.status,
        'status_display': fixture.get_status_display(),
        'period': fixture.period_display,
        'participants': live_payload['participants'],
        'clock': live_payload['clock'],
        'win_probability': live_payload['win_probability'],
        'individual_scoring_html': live_payload['individual_scoring_html'],
        'events': live_payload['events'],
        'entries': entries_data,
        'deleted_ids': list(deleted_entries),
        'last_id': max_id,
        'server_time': timezone.now().isoformat(),
    })


@require_GET
def commentary_stream_api(request, slug, fixture_id):
    """Server-Sent Events (SSE) real-time feed for live score and commentary updates."""
    fixture = get_object_or_404(
        Fixture.objects.select_related('tournament__sport'),
        id=fixture_id, tournament__slug=slug, is_removed=False
    )

    def event_stream():
        try:
            last_seen_id = int(request.GET.get('since_id', 0) or 0)
        except (TypeError, ValueError):
            last_seen_id = 0

        # Initial handshake
        handshake_payload = {
            'fixture_id': fixture.id,
            'status': fixture.status,
            'status_display': fixture.get_status_display(),
            'period': fixture.period_display,
        }
        yield f"event: connected\ndata: {json.dumps(handshake_payload)}\n\n"

        iteration = 0
        last_score_json = ""
        # Stream loop (cycles every 1.5 seconds, runs up to 30s before seamless reconnect)
        while iteration < 20:
            iteration += 1
            time.sleep(1.5)

            # Check for new commentary entries
            new_entries = list(CommentaryEntry.objects.filter(
                fixture_id=fixture.id, id__gt=last_seen_id, is_deleted=False
            ).select_related('author').order_by('id')[:20])

            if new_entries:
                for entry in new_entries:
                    last_seen_id = max(last_seen_id, entry.id)
                    entry_payload = {
                        'id': entry.id,
                        'text': entry.text,
                        'category': entry.category,
                        'category_display': entry.get_category_display(),
                        'author_id': entry.author_id,
                        'author_name': entry.author.display_name if entry.author else 'Commentator',
                        'author_username': entry.author.username if entry.author else '',
                        'created_at': entry.created_at.strftime('%H:%M'),
                        'created_iso': entry.created_at.isoformat(),
                        'match_clock': entry.match_clock,
                        'score_snapshot': entry.score_snapshot,
                    }
                    yield f"event: commentary_published\ndata: {json.dumps(entry_payload)}\n\n"
                    yield f"event: commentary\ndata: {json.dumps({'entries': [entry_payload]})}\n\n"

            # Check for deleted entries
            deleted_ids = list(CommentaryEntry.objects.filter(
                fixture_id=fixture.id, id__gt=last_seen_id, is_deleted=True
            ).values_list('id', flat=True))
            if deleted_ids:
                yield f"event: commentary_deleted\ndata: {json.dumps({'deleted_ids': deleted_ids})}\n\n"

            # Check official score state and player points in real-time
            latest_fx = Fixture.objects.filter(id=fixture.id).first()
            if latest_fx:
                from .views import build_fixture_live_payload
                live_payload = build_fixture_live_payload(latest_fx, request=request)
                score_str = json.dumps(live_payload)
                if score_str != last_score_json:
                    last_score_json = score_str
                    yield f"event: score_update\ndata: {score_str}\n\n"

            yield f": ping\n\n"

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache, no-transform'
    response['X-Accel-Buffering'] = 'no'
    return response
