from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from content.models import Achievement, Content, ContentComment, ContentLike, UserAchievement
from content.tasks import check_and_award_achievements_task

User = get_user_model()


def _make_user(email, complete_profile=True):
    user = User.objects.create_user(username=email, email=email, password='pw12345')
    profile = user.player_profile  # auto-created by accounts.signals.ensure_player_profile
    if complete_profile:
        from tournaments.models import Sport
        sport, _ = Sport.objects.get_or_create(
            slug='chess', defaults={'name': 'Chess', 'format_type': 'INDIVIDUAL'})
        user.first_name = 'Test'
        user.save(update_fields=['first_name'])
        profile.bio = 'Hi there'
        profile.save(update_fields=['bio'])
        profile.sports.add(sport)
        # email + first_name + bio + sports = 4/5 = 80%, meets is_profile_complete.
    return user, profile


class ContentLikeToggleTests(TestCase):
    def setUp(self):
        self.creator, _ = _make_user('creator@example.com')
        self.liker, _ = _make_user('liker@example.com')
        self.post = Content.objects.create(creator=self.creator, content_type='post', title='Hi')

    def test_like_then_unlike_toggles_and_updates_counter(self):
        self.client.force_login(self.liker)
        url = reverse('content_like', args=[self.post.pk])

        resp = self.client.post(url)
        self.assertEqual(resp.json()['liked'], True)
        self.assertEqual(resp.json()['like_count'], 1)
        self.assertTrue(ContentLike.objects.filter(content=self.post, user=self.liker).exists())

        resp = self.client.post(url)
        self.assertEqual(resp.json()['liked'], False)
        self.assertEqual(resp.json()['like_count'], 0)
        self.assertFalse(ContentLike.objects.filter(content=self.post, user=self.liker).exists())

    def test_like_notifies_creator_but_not_self_like(self):
        self.client.force_login(self.creator)
        self.client.post(reverse('content_like', args=[self.post.pk]))
        self.assertEqual(self.creator.notifications.count(), 0)

        self.client.force_login(self.liker)
        self.client.post(reverse('content_like', args=[self.post.pk]))
        self.assertEqual(self.creator.notifications.count(), 1)


class ContentCommentTests(TestCase):
    def setUp(self):
        self.creator, _ = _make_user('creator2@example.com')
        self.commenter, _ = _make_user('commenter@example.com')
        self.post = Content.objects.create(creator=self.creator, content_type='post', title='Hi')

    def test_comment_creates_row_and_increments_counter(self):
        self.client.force_login(self.commenter)
        resp = self.client.post(reverse('content_comment', args=[self.post.pk]), {'text': 'Nice one'})
        self.assertEqual(resp.status_code, 200)
        self.post.refresh_from_db()
        self.assertEqual(self.post.comment_count, 1)
        self.assertTrue(ContentComment.objects.filter(content=self.post, text='Nice one').exists())


class ContentUploadGatingTests(TestCase):
    def test_incomplete_profile_is_redirected_to_profile_edit(self):
        user, _ = _make_user('incomplete@example.com', complete_profile=False)
        self.client.force_login(user)
        resp = self.client.get(reverse('content_upload'))
        self.assertRedirects(resp, reverse('profile_edit'))

    def test_complete_profile_can_reach_upload_form(self):
        user, _ = _make_user('complete@example.com', complete_profile=True)
        self.client.force_login(user)
        resp = self.client.get(reverse('content_upload'))
        self.assertEqual(resp.status_code, 200)


class ProfileCompletionTests(TestCase):
    def test_completion_percentage_and_threshold(self):
        user, profile = _make_user('partial@example.com', complete_profile=False)
        self.assertFalse(profile.is_profile_complete)
        user.first_name = 'A'
        user.save(update_fields=['first_name'])
        profile.bio = 'bio'
        profile.profile_photo = None
        profile.save(update_fields=['bio'])
        # email + first_name + bio = 3/5 = 60%, still below the 80% threshold
        self.assertEqual(profile.completion_percentage, 60)
        self.assertFalse(profile.is_profile_complete)


class AchievementAwardingTests(TestCase):
    def test_criteria_met_awards_achievement_once(self):
        from accounts.models import OrganizerProfile
        from tournaments.models import Sport, Standing, Tournament
        import datetime

        user, profile = _make_user('achiever@example.com', complete_profile=False)
        organizer_user, _ = _make_user('organizer@example.com', complete_profile=False)
        organizer = OrganizerProfile.objects.create(user=organizer_user)
        sport = Sport.objects.create(name='Chess', slug='chess', format_type='INDIVIDUAL')
        t = Tournament.objects.create(
            name='Cup', sport=sport, organizer=organizer, start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 1, 2))
        Standing.objects.create(tournament=t, player=profile, played=3, won=3)
        achievement = Achievement.objects.create(name='First Win', criteria={'wins': 1})

        check_and_award_achievements_task(user.pk)
        self.assertTrue(UserAchievement.objects.filter(user=user, achievement=achievement).exists())

        # Running it again must not create a duplicate row.
        check_and_award_achievements_task(user.pk)
        self.assertEqual(UserAchievement.objects.filter(user=user, achievement=achievement).count(), 1)

    def test_criteria_not_met_awards_nothing(self):
        user, profile = _make_user('faller@example.com')
        Achievement.objects.create(name='Ten Wins', criteria={'wins': 10})
        check_and_award_achievements_task(user.pk)
        self.assertEqual(UserAchievement.objects.filter(user=user).count(), 0)
