from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import login_required_msg
from accounts.models import Notification
from tournaments.models import Tournament

from .forms import ContentCommentForm, ContentUploadForm
from .models import Content, ContentComment, ContentLike, ContentShare


def _visible_content():
    return Content.objects.filter(is_published=True, is_removed=False).select_related(
        'creator', 'tournament')


def feed(request):
    qs = _visible_content()
    tournament_slug = request.GET.get('tournament')
    if tournament_slug:
        qs = qs.filter(tournament__slug=tournament_slug)
    page = Paginator(qs, 20).get_page(request.GET.get('page'))
    liked_ids = set()
    if request.user.is_authenticated:
        liked_ids = set(ContentLike.objects.filter(
            user=request.user, content__in=page.object_list).values_list('content_id', flat=True))
    return render(request, 'content/feed.html', {
        'page': page, 'liked_ids': liked_ids, 'tournament_slug': tournament_slug,
    })


@login_required_msg
def content_upload(request):
    if not request.user.player_profile.is_profile_complete:
        messages.error(request, 'Complete your profile before sharing content.')
        return redirect('profile_edit')

    form = ContentUploadForm(request.POST or None, request.FILES or None)
    form.fields['tournament'].queryset = Tournament.objects.public()
    if request.method == 'POST' and form.is_valid():
        post = form.save(commit=False)
        post.creator = request.user
        post.save()
        messages.success(request, 'Posted!')
        return redirect('content_detail', pk=post.pk)
    return render(request, 'content/upload.html', {'form': form})


def content_detail(request, pk):
    post = get_object_or_404(_visible_content(), pk=pk)
    comment_form = ContentCommentForm()
    liked = request.user.is_authenticated and post.likes.filter(user=request.user).exists()
    return render(request, 'content/detail.html', {
        'post': post,
        'comments': post.comments.filter(is_removed=False).select_related('commenter'),
        'comment_form': comment_form,
        'liked': liked,
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
def content_view_ping(request, pk):
    Content.objects.filter(pk=pk, is_published=True, is_removed=False).update(
        view_count=F('view_count') + 1)
    return JsonResponse({'status': 'ok'})
