from io import BytesIO
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from tournaments.models import Sport, Tournament, Fixture, Highlight, HighlightImage
from accounts.models import OrganizerProfile

User = get_user_model()


def _make_dummy_image(name='test.jpg'):
    file_obj = BytesIO()
    image = Image.new('RGB', (100, 100), color=(73, 109, 137))
    image.save(file_obj, 'jpeg')
    file_obj.seek(0)
    return SimpleUploadedFile(name, file_obj.read(), content_type='image/jpeg')


class MatchHighlightsTests(TestCase):
    def setUp(self):
        self.sport = Sport.objects.create(name='Basketball', slug='basketball', format_type='ROUND_ROBIN')
        self.user = User.objects.create_user(email='org@example.com', username='org', password='password')
        self.org_profile = OrganizerProfile.objects.create(user=self.user, is_approved=True)
        today = timezone.now().date()
        self.tournament = Tournament.objects.create(
            name='Test Legends Cup',
            slug='test-legends-cup',
            sport=self.sport,
            organizer=self.org_profile,
            start_date=today,
            end_date=today + timedelta(days=5),
            status='PUBLISHED',
            format='KNOCKOUT'
        )
        self.fixture = Fixture.objects.create(
            tournament=self.tournament,
            round_name='Finals',
            sequence=1,
            status='COMPLETED'
        )

    def test_highlight_upload_multiple_photos_and_tag_player(self):
        self.client.login(email='org@example.com', password='password')

        img1 = _make_dummy_image('photo1.jpg')
        img2 = _make_dummy_image('photo2.jpg')

        url = reverse('highlight_manage', args=[self.tournament.slug, self.fixture.id])
        data = {
            'title': 'Championship Winning Buzzer Beater',
            'description': 'Amazing game winner with 2 seconds left!',
            'best_performer': 'Michael Jordan, Scottie Pippen',
            'photos': [img1, img2],
            'publish': '1'
        }
        res = self.client.post(url, data, follow=True)
        self.assertEqual(res.status_code, 200)

        highlight = Highlight.objects.filter(fixture=self.fixture).first()
        self.assertIsNotNone(highlight)
        self.assertEqual(highlight.title, 'Championship Winning Buzzer Beater')
        self.assertEqual(highlight.best_performer, 'Michael Jordan, Scottie Pippen')
        self.assertIsNotNone(highlight.published_at)

        # Check gallery images
        self.assertEqual(highlight.gallery_images.count(), 2)
        self.assertGreaterEqual(len(highlight.all_images), 2)

        # Verify reflection on match detail page
        match_url = reverse('match_detail', args=[self.tournament.slug, self.fixture.id])
        res_match = self.client.get(match_url)
        self.assertEqual(res_match.status_code, 200)
        self.assertContains(res_match, 'Championship Winning Buzzer Beater')
        self.assertContains(res_match, 'Michael Jordan, Scottie Pippen')
        self.assertContains(res_match, 'Match Highlights')

    def test_highlight_update_photo_reflects_in_match(self):
        self.client.login(email='org@example.com', password='password')

        img1 = _make_dummy_image('initial.jpg')
        h = Highlight.objects.create(
            tournament=self.tournament,
            fixture=self.fixture,
            title='Initial Highlight',
            description='Old recap',
            best_performer='Old Player',
            image=img1,
            created_by=self.user
        )

        # Update highlight with new photo and new player tag
        new_img = _make_dummy_image('updated.jpg')
        url = reverse('highlight_manage', args=[self.tournament.slug, self.fixture.id])
        data = {
            'title': 'Updated Match Recap',
            'description': 'Updated description',
            'best_performer': 'New MVP Player',
            'image': new_img,
            'publish': '1'
        }
        res = self.client.post(url, data, follow=True)
        self.assertEqual(res.status_code, 200)

        h.refresh_from_db()
        self.assertEqual(h.title, 'Updated Match Recap')
        self.assertEqual(h.best_performer, 'New MVP Player')

        # Check match detail reflects updated content
        match_url = reverse('match_detail', args=[self.tournament.slug, self.fixture.id])
        res_match = self.client.get(match_url)
        self.assertContains(res_match, 'Updated Match Recap')
        self.assertContains(res_match, 'New MVP Player')
