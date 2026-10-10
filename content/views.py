from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import F, Q, Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import login_required_msg
from accounts.models import Notification, UserFollow, OrganizerProfile, PlayerProfile
from tournaments.models import Fixture, Tournament, Sport, Team

from .forms import ContentCommentForm, ContentUploadForm
from .models import Content, ContentComment, ContentLike, ContentMedia, ContentShare, ContentBookmark


def _visible_content():
    return Content.objects.filter(is_published=True, is_removed=False).select_related(
        'creator', 'tournament__sport', 'team_author__sport', 'organizer_author'
    ).prefetch_related('media_items', 'tagged_users')


def feed(request):
    # 1. Live Matches (Authoritative match scoring & fixtures)
    live_matches = list(
        Fixture.objects.filter(status='LIVE', is_removed=False)
        .select_related('tournament__sport', 'event_category')
        .prefetch_related('participants__team', 'participants__player__user')
        .order_by('scheduled_time', 'id')[:8]
    )
    upcoming_matches = []
    if not live_matches:
        upcoming_matches = list(
            Fixture.objects.filter(status='SCHEDULED', is_removed=False, scheduled_time__gte=timezone.now())
            .select_related('tournament__sport', 'event_category')
            .prefetch_related('participants__team', 'participants__player__user')
            .order_by('scheduled_time', 'id')[:4]
        )

    # 2. Popular / Featured Tournaments
    popular_tournaments = list(
        Tournament.objects.public()
        .filter(status__in=['PUBLISHED', 'REGISTRATION_OPEN', 'ONGOING', 'COMPLETED'])
        .select_related('sport', 'venue')
        .annotate(
            entries_count=Count('team_entries', filter=Q(team_entries__status__in=['APPROVED', 'CONFIRMED']), distinct=True) +
                          Count('registrations', filter=Q(registrations__status__in=['APPROVED', 'CONFIRMED']), distinct=True)
        )
        .order_by('-entries_count', '-start_date')[:10]
    )

    # 3. Feed Filtering & Category Selection
    qs = _visible_content()

    category = request.GET.get('category', 'for_you').strip().lower()
    media_type = request.GET.get('media_type', 'all').strip().lower()
    sort_by = request.GET.get('sort', 'latest').strip().lower()
    sport_filter = request.GET.get('sport', '').strip()
    tournament_slug = request.GET.get('tournament', '').strip()

    if tournament_slug:
        qs = qs.filter(tournament__slug=tournament_slug)

    # Category filters
    if category == 'tournaments':
        qs = qs.filter(Q(tournament__isnull=False) | Q(title__icontains='tournament'))
    elif category == 'teams':
        qs = qs.filter(team_author__isnull=False)
    elif category == 'players':
        qs = qs.filter(creator__player_profile__isnull=False, team_author__isnull=True, organizer_author__isnull=True)
    elif category == 'organizers':
        qs = qs.filter(organizer_author__isnull=False)
    elif category == 'saved':
        if request.user.is_authenticated:
            qs = qs.filter(bookmarks__user=request.user)
        else:
            qs = qs.none()
    elif category == 'for_you':
        pass

    # Media type sub-filters
    if media_type == 'posts':
        qs = qs.filter(content_type__in=['photo', 'post', 'carousel'])
    elif media_type == 'highlights':
        qs = qs.filter(Q(content_type='short') | Q(aspect_ratio='9/16'))
    elif media_type == 'vlogs':
        qs = qs.filter(Q(content_type='full_video') | Q(aspect_ratio='16/9') | Q(youtube_url__gt=''))

    # Sport shortcut filter
    if sport_filter:
        qs = qs.filter(Q(tournament__sport__slug=sport_filter) | Q(team_author__sport__slug=sport_filter))

    # Sort order
    if sort_by == 'popular':
        qs = qs.order_by('-like_count', '-comment_count', '-created_at')
    else:
        qs = qs.order_by('-created_at')

    # Pagination
    paginator = Paginator(qs, 12)
    page_number = request.GET.get('page', 1)
    page = paginator.get_page(page_number)

    # Interaction states for the current page
    liked_ids = set()
    bookmarked_ids = set()
    following_user_ids = set()
    if request.user.is_authenticated:
        post_ids = [p.pk for p in page.object_list]
        liked_ids = set(ContentLike.objects.filter(user=request.user, content_id__in=post_ids).values_list('content_id', flat=True))
        bookmarked_ids = set(ContentBookmark.objects.filter(user=request.user, content_id__in=post_ids).values_list('content_id', flat=True))
        following_user_ids = set(UserFollow.objects.filter(follower=request.user).values_list('following_id', flat=True))

    # 4. Contextual Discovery (Right Column)
    # 4a. Upcoming Near You
    user_city = None
    if request.user.is_authenticated and hasattr(request.user, 'player_profile'):
        user_city = request.user.player_profile.current_city or request.user.player_profile.home_city

    upcoming_qs = Tournament.objects.public().filter(
        status__in=['PUBLISHED', 'REGISTRATION_OPEN', 'ONGOING'],
        start_date__gte=timezone.now().date()
    ).select_related('sport', 'venue').order_by('start_date')

    if user_city:
        city_tournaments = list(upcoming_qs.filter(Q(city__iexact=user_city) | Q(venue__city__iexact=user_city))[:4])
        upcoming_near_you = city_tournaments or list(upcoming_qs[:4])
    else:
        upcoming_near_you = list(upcoming_qs[:4])

    # 4b. Suggested for You (Real platform entities: Organizers, Teams, Players)
    suggested_entities = []
    # Organizers
    org_qs = OrganizerProfile.objects.filter(is_approved=True).select_related('user').annotate(
        t_count=Count('tournaments')
    ).order_by('-t_count')
    for org in org_qs[:2]:
        if request.user.is_authenticated and org.user_id == request.user.id:
            continue
        avatar = org.profile_photo.url if getattr(org, 'profile_photo', None) else ''
        suggested_entities.append({
            'type': 'ORGANIZER',
            'name': org.organization_name or org.user.display_name,
            'meta': f"Organizer · {org.t_count} Tournament{'s' if org.t_count != 1 else ''}",
            'avatar': avatar,
            'initials': (org.organization_name or org.user.display_name)[:2].upper(),
            'url': reverse('organizer_public', args=[org.pk]),
            'user_id': org.user_id,
            'is_following': org.user_id in following_user_ids,
        })

    # Teams
    team_qs = Team.objects.all().select_related('sport').annotate(m_count=Count('memberships')).order_by('-m_count')
    for team in team_qs[:2]:
        avatar = team.logo.url if team.logo else ''
        owner_user_id = team.owner.user_id if team.owner else None
        suggested_entities.append({
            'type': 'TEAM',
            'name': team.name,
            'meta': f"Team · {team.m_count} Member{'s' if team.m_count != 1 else ''}",
            'avatar': avatar,
            'initials': team.name[:2].upper(),
            'url': reverse('team_detail', args=[team.pk]),
            'user_id': owner_user_id,
            'is_following': (owner_user_id in following_user_ids) if owner_user_id else False,
        })

    # Players
    p_qs = PlayerProfile.objects.select_related('user').prefetch_related('sports')
    if request.user.is_authenticated:
        p_qs = p_qs.exclude(user=request.user)
    for p in p_qs.order_by('-created_at')[:2]:
        avatar = p.profile_photo.url if p.profile_photo else ''
        first_sport = p.sports.first()
        meta_role = first_sport.name if first_sport else 'Player'
        suggested_entities.append({
            'type': 'PLAYER',
            'name': p.user.display_name,
            'meta': f"Player · {meta_role}",
            'avatar': avatar,
            'initials': p.user.display_name[:2].upper(),
            'url': reverse('player_public', args=[p.pk]),
            'user_id': p.user_id,
            'is_following': p.user_id in following_user_ids,
        })

    # 4c. Popular by Sport (Grid)
    all_sports = list(Sport.objects.all().order_by('name'))
    popular_sports = all_sports[:8]

    return render(request, 'content/feed.html', {
        'page': page,
        'liked_ids': liked_ids,
        'bookmarked_ids': bookmarked_ids,
        'category': category,
        'media_type': media_type,
        'sort_by': sort_by,
        'sport_filter': sport_filter,
        'tournament_slug': tournament_slug,
        'live_matches': live_matches,
        'upcoming_matches': upcoming_matches,
        'popular_tournaments': popular_tournaments,
        'upcoming_near_you': upcoming_near_you,
        'user_city': user_city,
        'suggested_entities': suggested_entities,
        'popular_sports': popular_sports,
        'all_sports': all_sports,
    })


@login_required_msg
def content_upload(request):
    is_entity_author = bool(request.POST.get('organizer_author') or request.POST.get('team_author'))
    if not is_entity_author and not request.user.is_staff:
        if hasattr(request.user, 'player_profile') and not request.user.player_profile.is_profile_complete:
            messages.error(request, 'Complete your profile before sharing content.')
            return redirect('profile_edit')

    form = ContentUploadForm(request.POST or None, request.FILES or None)
    form.fields['tournament'].queryset = Tournament.objects.public()
    if request.method == 'POST' and form.is_valid():
        post = form.save(commit=False)
        post.creator = request.user
        
        team_id = request.POST.get('team_author')
        if team_id:
            from tournaments.models import Team
            from tournaments.team_views import _can_manage_team
            t = Team.objects.filter(id=team_id).first()
            if t and _can_manage_team(t, request.user):
                post.team_author = t
                
        org_id = request.POST.get('organizer_author')
        if org_id:
            if hasattr(request.user, 'organizer_profile') and str(request.user.organizer_profile.id) == str(org_id):
                post.organizer_author = request.user.organizer_profile

        aspect_ratio = request.POST.get('aspect_ratio')
        if aspect_ratio:
            post.aspect_ratio = aspect_ratio

        youtube_url = request.POST.get('youtube_url', '').strip()
        if youtube_url:
            post.youtube_url = youtube_url
            post.content_type = Content.TYPE_FULL_VIDEO

        duration = request.POST.get('video_duration')
        if duration:
            try:
                post.video_duration = int(float(duration))
            except (ValueError, TypeError):
                pass

        # Collect uploaded files (supports unlimited carousel slides)
        files = request.FILES.getlist('media_files')
        if not files and request.FILES.get('media_file'):
            files = [request.FILES['media_file']]

        if files:
            if len(files) > 1:
                post.content_type = Content.TYPE_CAROUSEL
            elif files[0].name.lower().split('.')[-1] in ['mp4', 'mov', 'webm', 'mkv', 'm4v', 'avi']:
                if post.aspect_ratio == '9/16' or request.POST.get('content_type') == 'short':
                    post.content_type = Content.TYPE_SHORT
                else:
                    post.content_type = Content.TYPE_FULL_VIDEO if post.aspect_ratio == '16/9' else Content.TYPE_VIDEO
            else:
                post.content_type = Content.TYPE_PHOTO
        elif not post.content_type:
            post.content_type = Content.TYPE_POST

        post.save()

        # Create ContentMedia items for all uploaded files
        if files:
            media_objs = []
            for idx, f in enumerate(files):
                media_objs.append(ContentMedia.objects.create(content=post, media_file=f, order=idx))
            if media_objs:
                post.media_file = media_objs[0].media_file.name
                post.save(update_fields=['media_file'])

        # Process user @mentions in description
        if post.description:
            import re
            from django.contrib.auth import get_user_model
            User = get_user_model()
            usernames = re.findall(r'@([A-Za-z0-9_.-]+)', post.description)
            if usernames:
                tagged_users = list(User.objects.filter(username__in=usernames).exclude(id=request.user.id))
                if tagged_users:
                    post.tagged_users.add(*tagged_users)
                    for u in tagged_users:
                        Notification.push(
                            u,
                            f'{request.user.display_name} tagged you in a post.',
                            url=post.get_absolute_url(),
                            verb='content_tagged'
                        )

        messages.success(request, 'Posted!')
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.accepts('application/json'):
            return JsonResponse({'success': True, 'redirect_url': post.get_absolute_url()})
        return redirect('content_detail', pk=post.pk)

    if request.method == 'POST' and (request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json'):
        return JsonResponse({'success': False, 'errors': form.errors}, status=400)
    return render(request, 'content/upload.html', {'form': form})


def search_users(request):
    """API endpoint to search users for @mentioning."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'users': []})

    qs = User.objects.filter(username__icontains=q).select_related('player_profile')[:10]
    data = []
    for u in qs:
        avatar = ''
        if hasattr(u, 'player_profile') and u.player_profile.profile_photo:
            avatar = u.player_profile.profile_photo.url
        data.append({
            'username': u.username,
            'display_name': u.display_name,
            'avatar': avatar,
        })
    return JsonResponse({'users': data})


def content_detail(request, pk):
    post = get_object_or_404(_visible_content(), pk=pk)
    comment_form = ContentCommentForm()
    liked = request.user.is_authenticated and post.likes.filter(user=request.user).exists()
    bookmarked = request.user.is_authenticated and post.bookmarks.filter(user=request.user).exists()
    is_following = request.user.is_authenticated and UserFollow.objects.filter(
        follower=request.user, following=post.creator
    ).exists()

    follower_count = post.creator.followers_users.count()

    # Section A: More from this tournament (if associated with a tournament)
    more_from_tournament = []
    if post.tournament:
        more_from_tournament = list(
            Content.objects.filter(is_published=True, is_removed=False, tournament=post.tournament)
            .exclude(id=post.id)
            .select_related('creator', 'tournament__sport')
            .order_by('-created_at')[:4]
        )

    # Fallback to same creator if no tournament content
    if not more_from_tournament:
        more_from_tournament = list(
            Content.objects.filter(is_published=True, is_removed=False, creator=post.creator)
            .exclude(id=post.id)
            .select_related('creator', 'tournament__sport')
            .order_by('-created_at')[:4]
        )

    excluded_ids = [post.id] + [c.id for c in more_from_tournament]

    # Section B: Related content based on sport or general recommendations (strictly no duplicates)
    related_content = []
    if post.tournament and post.tournament.sport:
        related_content = list(
            Content.objects.filter(is_published=True, is_removed=False, tournament__sport=post.tournament.sport)
            .exclude(id__in=excluded_ids)
            .select_related('creator', 'tournament__sport')
            .order_by('-view_count', '-created_at')[:4]
        )

    if len(related_content) < 4:
        needed = 4 - len(related_content)
        extra_excluded = excluded_ids + [c.id for c in related_content]
        fallback = list(
            Content.objects.filter(is_published=True, is_removed=False)
            .exclude(id__in=extra_excluded)
            .select_related('creator', 'tournament__sport')
            .order_by('-view_count', '-created_at')[:needed]
        )
        related_content.extend(fallback)

    # Contextual hashtags
    import re
    hashtags = []
    if post.description:
        raw_tags = re.findall(r'#([A-Za-z0-9_]+)', post.description)
        hashtags = [f'#{tag}' for tag in raw_tags[:5]]
    if not hashtags:
        if post.tournament:
            hashtags.append(f"#{post.tournament.name.replace(' ', '')}")
            if post.tournament.sport:
                hashtags.append(f"#{post.tournament.sport.name.replace(' ', '')}")
        hashtags.append(f"#{post.get_content_type_display().replace(' ', '')}")

    return render(request, 'content/detail.html', {
        'post': post,
        'comments': post.comments.filter(is_removed=False).select_related('commenter').order_by('-created_at'),
        'comment_form': comment_form,
        'liked': liked,
        'bookmarked': bookmarked,
        'is_following': is_following,
        'follower_count': follower_count,
        'more_from_tournament': more_from_tournament,
        'related_content': related_content,
        'hashtags': hashtags,
    })


@require_POST
@login_required_msg
def content_like(request, pk):
    post = get_object_or_404(_visible_content(), pk=pk)
    existing = ContentLike.objects.filter(content=post, user=request.user).first()
    if existing:
        existing.delete()
        Content.objects.filter(pk=post.pk).update(like_count=F('like_count') - 1)
        liked = False
    else:
        ContentLike.objects.create(content=post, user=request.user)
        Content.objects.filter(pk=post.pk).update(like_count=F('like_count') + 1)
        liked = True
        if post.creator_id != request.user.id:
            Notification.push(post.creator, f'{request.user.display_name} liked your post.',
                              url=post.get_absolute_url(), verb='content_liked')
    post.refresh_from_db(fields=['like_count'])
    return JsonResponse({'liked': liked, 'like_count': post.like_count})


@require_POST
@login_required_msg
def content_comment(request, pk):
    post = get_object_or_404(_visible_content(), pk=pk)
    form = ContentCommentForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'errors': form.errors}, status=400)
    comment = form.save(commit=False)
    comment.content = post
    comment.commenter = request.user
    comment.save()
    Content.objects.filter(pk=post.pk).update(comment_count=F('comment_count') + 1)
    if post.creator_id != request.user.id:
        Notification.push(post.creator, f'{request.user.display_name} commented on your post.',
                          url=post.get_absolute_url(), verb='content_commented')
    return JsonResponse({
        'comment': {
            'text': comment.text,
            'commenter': request.user.display_name,
            'created_at': comment.created_at.strftime('%d %b, %H:%M'),
        },
    })


@require_POST
@login_required_msg
def content_share(request, pk):
    post = get_object_or_404(_visible_content(), pk=pk)
    share_type = request.POST.get('share_type', ContentShare.SHARE_LINK)
    ContentShare.objects.create(content=post, shared_by=request.user, share_type=share_type)
    Content.objects.filter(pk=post.pk).update(share_count=F('share_count') + 1)
    return JsonResponse({'url': request.build_absolute_uri(post.get_absolute_url())})


@require_POST
@login_required_msg
def content_bookmark(request, pk):
    post = get_object_or_404(_visible_content(), pk=pk)
    existing = ContentBookmark.objects.filter(content=post, user=request.user).first()
    if existing:
        existing.delete()
        bookmarked = False
    else:
        ContentBookmark.objects.create(content=post, user=request.user)
        bookmarked = True
    return JsonResponse({'bookmarked': bookmarked})


@require_POST
def content_view_ping(request, pk):
    Content.objects.filter(pk=pk, is_published=True, is_removed=False).update(
        view_count=F('view_count') + 1)
    return JsonResponse({'status': 'ok'})
