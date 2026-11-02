from io import BytesIO
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import OrganizerProfile
from content.models import Content, ContentMedia
from content.validators import CONTENT_MEDIA_MAX_BYTES
from tournaments.models import Sport

User = get_user_model()


def _make_user(email, complete_profile=True):
    user = User.objects.create_user(username=email, email=email, password='pw12345')
    profile = user.player_profile
    if complete_profile:
        sport, _ = Sport.objects.get_or_create(
            slug='basketball', defaults={'name': 'Basketball', 'format_type': 'TEAM'})
        user.first_name = 'Player'
        user.save(update_fields=['first_name'])
        profile.bio = 'Basketball enthusiast'
        profile.save(update_fields=['bio'])
        profile.sports.add(sport)
    return user, profile


class FullLengthVideoUploadTests(TestCase):
    def setUp(self):
        self.user, self.profile = _make_user('video_uploader@example.com', complete_profile=True)
        self.client.force_login(self.user)

    def test_full_length_video_file_upload_success(self):
        video_content = b'fake-mp4-video-content-stream-1234567890'
        video_file = SimpleUploadedFile('match_highlights_full.mp4', video_content, content_type='video/mp4')

        resp = self.client.post(reverse('content_upload'), {
            'content_type': 'full_video',
            'aspect_ratio': '16/9',
            'title': 'Grand Final Full Match',
            'description': 'Full game coverage of the championship match.',
            'media_file': video_file,
            'video_duration': '1800',
        })

        self.assertEqual(resp.status_code, 302)
        post = Content.objects.filter(title='Grand Final Full Match').first()
        self.assertIsNotNone(post)
        self.assertEqual(post.content_type, Content.TYPE_FULL_VIDEO)
        self.assertEqual(post.aspect_ratio, '16/9')
        self.assertEqual(post.video_duration, 1800)
        self.assertTrue(post.is_video)
        self.assertTrue(bool(post.media_file))

        # Check that ContentMedia item is created and contains the non-empty file
        media_item = post.media_items.first()
        self.assertIsNotNone(media_item)
        self.assertTrue(media_item.is_video)
        with media_item.media_file.open('rb') as f:
            data = f.read()
            self.assertEqual(data, video_content)

    def test_full_video_upload_via_media_files_ajax(self):
        video_content = b'ajax-video-content-stream'
        video_file = SimpleUploadedFile('full_vlog.mp4', video_content, content_type='video/mp4')

        resp = self.client.post(
            reverse('content_upload'),
            {
                'content_type': 'full_video',
                'aspect_ratio': '16/9',
                'title': 'Behind the Scenes Full Episode',
                'media_files': [video_file],
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        post = Content.objects.filter(title='Behind the Scenes Full Episode').first()
        self.assertIsNotNone(post)
        self.assertEqual(post.content_type, Content.TYPE_FULL_VIDEO)
        self.assertEqual(post.aspect_ratio, '16/9')
        self.assertTrue(post.is_video)

    def test_full_video_youtube_url_upload(self):
        resp = self.client.post(reverse('content_upload'), {
            'content_type': 'full_video',
            'aspect_ratio': '16/9',
            'title': 'Live Stream Replay',
            'youtube_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        })

        self.assertEqual(resp.status_code, 302)
        post = Content.objects.filter(title='Live Stream Replay').first()
        self.assertIsNotNone(post)
        self.assertEqual(post.content_type, Content.TYPE_FULL_VIDEO)
        self.assertEqual(post.aspect_ratio, '16/9')
        self.assertEqual(post.youtube_video_id, 'dQw4w9WgXcQ')
        self.assertEqual(post.youtube_embed_url, 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ')
        self.assertTrue(post.is_video)

    def test_full_video_youtube_live_and_shortened_urls(self):
        # YouTube live url
        p1 = Content(creator=self.user, youtube_url='https://www.youtube.com/live/AbCdEf12345')
        self.assertEqual(p1.youtube_video_id, 'AbCdEf12345')

        # youtu.be short url with query string
        p2 = Content(creator=self.user, youtube_url='https://youtu.be/AbCdEf12345?feature=shared')
        self.assertEqual(p2.youtube_video_id, 'AbCdEf12345')

        # Mobile youtube url with timestamp
        p3 = Content(creator=self.user, youtube_url='https://m.youtube.com/watch?v=AbCdEf12345&t=42s')
        self.assertEqual(p3.youtube_video_id, 'AbCdEf12345')

    def test_youtube_url_auto_prepends_https(self):
        resp = self.client.post(reverse('content_upload'), {
            'content_type': 'full_video',
            'title': 'No Scheme URL Test',
            'youtube_url': 'www.youtube.com/watch?v=dQw4w9WgXcQ',
        })
        self.assertEqual(resp.status_code, 302)
        post = Content.objects.filter(title='No Scheme URL Test').first()
        self.assertIsNotNone(post)
        self.assertTrue(post.youtube_url.startswith('https://'))

    def test_video_file_size_exceeded_rejected(self):
        from content.forms import ContentUploadForm
        oversized_file = SimpleUploadedFile('huge_match.mp4', b'small-content', content_type='video/mp4')
        oversized_file.size = CONTENT_MEDIA_MAX_BYTES + 1024 * 1024  # 501MB

        form = ContentUploadForm(
            data={'content_type': 'full_video', 'title': 'Too Big Match'},
            files={'media_file': oversized_file}
        )
        self.assertFalse(form.is_valid())
        self.assertIn('media_file', form.errors)
        self.assertTrue(any('too large' in str(e).lower() for e in form.errors['media_file']))

    def test_unsupported_video_extension_rejected(self):
        bad_file = SimpleUploadedFile('bad_video.exe', b'binary-code', content_type='application/octet-stream')
        resp = self.client.post(reverse('content_upload'), {
            'content_type': 'full_video',
            'title': 'Malicious Post',
            'media_file': bad_file,
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(resp.status_code, 400)
        errors = resp.json()['errors']
        self.assertIn('media_file', errors)
        self.assertTrue(any('unsupported' in str(e).lower() for e in errors['media_file']))

    def test_incomplete_profile_ajax_returns_400_json(self):
        incomplete_user, _ = _make_user('incomplete_ajax@example.com', complete_profile=False)
        self.client.force_login(incomplete_user)

        resp = self.client.post(
            reverse('content_upload'),
            {'content_type': 'full_video', 'title': 'Test Post'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertIn('__all__', data['errors'])
        self.assertTrue(any('complete' in e.lower() for e in data['errors']['__all__']))

    def test_organizer_user_can_upload_without_player_profile(self):
        org_user = User.objects.create_user(username='org_only', email='org_only@example.com', password='pw')
        org_profile = OrganizerProfile.objects.create(user=org_user, organization_name='Basketball League')
        self.client.force_login(org_user)

        resp = self.client.post(reverse('content_upload'), {
            'content_type': 'full_video',
            'aspect_ratio': '16/9',
            'title': 'Tournament Organizer Stream',
            'youtube_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'organizer_author': str(org_profile.id),
        })
        self.assertEqual(resp.status_code, 302)
        post = Content.objects.filter(title='Tournament Organizer Stream').first()
        self.assertIsNotNone(post)
        self.assertEqual(post.organizer_author, org_profile)

    def test_feed_and_detail_views_render_full_video(self):
        post = Content.objects.create(
            creator=self.user,
            content_type=Content.TYPE_FULL_VIDEO,
            aspect_ratio='16/9',
            title='Championship Replay',
            youtube_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        )

        # Feed
        feed_resp = self.client.get(reverse('content_feed'))
        self.assertEqual(feed_resp.status_code, 200)
        self.assertContains(feed_resp, 'Championship Replay')
        self.assertContains(feed_resp, 'youtube-nocookie.com/embed/dQw4w9WgXcQ')

        # Detail
        detail_resp = self.client.get(reverse('content_detail', args=[post.pk]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, 'media-container--16-9')
        self.assertContains(detail_resp, 'youtube-nocookie.com/embed/dQw4w9WgXcQ')
