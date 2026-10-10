from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import CommentatorProfile, OrganizerProfile
from tournaments import constants as C
from tournaments.models import (
    Fixture,
    FixtureParticipant,
    FixtureCommentatorAssignment,
    TournamentCommentatorRegistration,
    CommentaryEntry,
    Sport,
    Tournament,
)

User = get_user_model()


class CommentatorAndCommentaryTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Sport
        self.sport, _ = Sport.objects.get_or_create(slug='basketball', defaults={'name': 'Basketball', 'format_type': 'TEAM'})

        # Create Organizer User & Profile
        self.organizer_user = User.objects.create_user(
            username='organizer_lead',
            email='organizer_lead@example.com',
            password='password123',
            first_name='Lead',
            last_name='Organizer'
        )
        self.organizer_profile = OrganizerProfile.objects.create(
            user=self.organizer_user,
            organization_name='Championship League',
            is_approved=True
        )

        # Create Commentator Users
        self.comm_user1 = User.objects.create_user(
            username='comm_mike',
            email='mike@broadcast.com',
            password='password123',
            first_name='Mike',
            last_name='Voice'
        )
        self.comm_user2 = User.objects.create_user(
            username='comm_sarah',
            email='sarah@broadcast.com',
            password='password123',
            first_name='Sarah',
            last_name='Analyst'
        )

        # Create Regular Unrelated User
        self.regular_user = User.objects.create_user(
            username='regular_fan',
            email='fan@example.com',
            password='password123',
            first_name='Regular',
            last_name='Fan'
        )

        # Create Tournament
        self.tournament = Tournament.objects.create(
            organizer=self.organizer_profile,
            sport=self.sport,
            name='National Basketball Open',
            slug='national-basketball-open',
            format=C.FORMAT_KNOCKOUT,
            status='PUBLISHED',
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=7)).date()
        )

        # Create Fixture
        self.fixture = Fixture.objects.create(
            tournament=self.tournament,
            round_no=1,
            round_name='Quarterfinal 1',
            status='LIVE',
            scheduled_time=timezone.now()
        )
        self.p1 = FixtureParticipant.objects.create(fixture=self.fixture, slot=0, label='Lakers Pro', score=42)
        self.p2 = FixtureParticipant.objects.create(fixture=self.fixture, slot=1, label='Celtics Elite', score=39)

    def test_commentator_profile_creation_and_properties(self):
        """User creates and edits commentator profile, verifies public portfolio view."""
        self.assertFalse(self.comm_user1.has_commentator_profile)

        # Unauthenticated access redirects to login
        onboard_url = reverse('commentator_onboarding')
        resp = self.client.get(onboard_url)
        self.assertEqual(resp.status_code, 302)

        # Login and onboard
        self.client.force_login(self.comm_user1)
        resp = self.client.get(onboard_url)
        self.assertEqual(resp.status_code, 200)

        # POST onboarding
        post_data = {
            'bio': 'Passionate basketball play-by-play announcer with 5 years experience.',
            'experience_level': 'PROFESSIONAL',
            'years_experience': 5,
            'languages': 'English, Spanish',
            'social_handle': '@mikevoice',
            'sample_reel_url': 'https://youtube.com/watch?v=sample123',
            'sports': [self.sport.id],
            'is_available': True,
        }
        resp = self.client.post(onboard_url, post_data)
        self.assertEqual(resp.status_code, 302)

        # Verify profile created
        self.comm_user1.refresh_from_db()
        self.assertTrue(self.comm_user1.has_commentator_profile)
        profile = self.comm_user1.commentator_profile
        self.assertEqual(profile.experience_level, 'PROFESSIONAL')
        self.assertEqual(profile.languages, 'English, Spanish')
        self.assertTrue(profile.sports.filter(id=self.sport.id).exists())

        # Public profile access
        public_url = reverse('commentator_public', kwargs={'pk': profile.pk})
        self.client.logout()
        resp = self.client.get(public_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Mike Voice')
        self.assertContains(resp, 'Passionate basketball play-by-play announcer')

    def test_commentator_portal_and_dashboard_access(self):
        """Commentator dashboard directs unonboarded users and serves active commentators."""
        dash_url = reverse('commentator_dashboard')

        # Unauthenticated
        resp = self.client.get(dash_url)
        self.assertEqual(resp.status_code, 302)

        # User without commentator profile is redirected to onboarding
        self.client.force_login(self.regular_user)
        resp = self.client.get(dash_url)
        self.assertRedirects(resp, reverse('commentator_onboarding'))

        # Create profile for comm_sarah
        CommentatorProfile.objects.create(
            user=self.comm_user2,
            bio='Expert analyst',
            experience_level='REGIONAL',
            is_available=True
        )
        self.client.force_login(self.comm_user2)
        resp = self.client.get(dash_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Commentator Portal')
        self.assertContains(resp, 'Available to Cast')

    def test_organizer_tournament_commentator_pool_management(self):
        """Organizer searches, adds, and removes commentators from tournament pool."""
        # Setup commentator profiles
        cp1 = CommentatorProfile.objects.create(
            user=self.comm_user1,
            bio='Voice 1',
            experience_level='PROFESSIONAL',
            is_available=True
        )
        cp1.sports.add(self.sport)

        search_url = reverse('tournament_commentators_search_api', kwargs={'slug': self.tournament.slug})
        add_url = reverse('tournament_commentator_add', kwargs={'slug': self.tournament.slug})

        # Regular user cannot search or add to pool (redirected to organizer_status)
        self.client.force_login(self.regular_user)
        resp = self.client.get(search_url + '?q=mike')
        self.assertEqual(resp.status_code, 302)
        resp = self.client.post(add_url, {'user_id': self.comm_user1.id})
        self.assertEqual(resp.status_code, 302)

        # Organizer can search
        self.client.force_login(self.organizer_user)
        resp = self.client.get(search_url + '?q=mike')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['username'], 'comm_mike')

        # Organizer adds commentator to pool
        resp = self.client.post(add_url, {'user_id': self.comm_user1.id})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(TournamentCommentatorRegistration.objects.filter(
            tournament=self.tournament, user=self.comm_user1, is_active=True
        ).exists())

        # Adding same commentator again is handled gracefully
        resp = self.client.post(add_url, {'user_id': self.comm_user1.id})
        self.assertEqual(resp.status_code, 302)

        # Organizer removes commentator from pool
        remove_url = reverse('tournament_commentator_remove', kwargs={
            'slug': self.tournament.slug, 'user_id': self.comm_user1.id
        })
        resp = self.client.post(remove_url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(TournamentCommentatorRegistration.objects.filter(
            tournament=self.tournament, user=self.comm_user1, is_active=True
        ).exists())

    def test_fixture_commentator_assignment_and_auto_assign(self):
        """Organizer manually assigns and auto-assigns pool commentators to fixtures."""
        CommentatorProfile.objects.create(user=self.comm_user1, experience_level='PROFESSIONAL', is_available=True)
        CommentatorProfile.objects.create(user=self.comm_user2, experience_level='PROFESSIONAL', is_available=True)
        TournamentCommentatorRegistration.objects.create(tournament=self.tournament, user=self.comm_user1, is_active=True)
        TournamentCommentatorRegistration.objects.create(tournament=self.tournament, user=self.comm_user2, is_active=True)

        self.client.force_login(self.organizer_user)

        # Auto-assign
        auto_url = reverse('tournament_commentators_auto_assign', kwargs={'slug': self.tournament.slug})
        resp = self.client.post(auto_url, {'role': 'LEAD', 'overwrite_existing': '1'})
        self.assertEqual(resp.status_code, 302)

        # Fixture should now have an assigned commentator
        self.assertTrue(FixtureCommentatorAssignment.objects.filter(
            fixture=self.fixture, user__in=[self.comm_user1, self.comm_user2]
        ).exists())

        # Manual assign specific commentator
        assign_url = reverse('fixture_commentator_assign', kwargs={
            'slug': self.tournament.slug, 'fixture_id': self.fixture.id
        })
        resp = self.client.post(assign_url, {
            'user_id': self.comm_user2.id,
            'role': 'COLOR',
            'notes': 'Court-side analysis'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(FixtureCommentatorAssignment.objects.filter(
            fixture=self.fixture, user=self.comm_user2, role='COLOR'
        ).exists())

        # Unassign
        unassign_url = reverse('fixture_commentator_unassign', kwargs={
            'slug': self.tournament.slug, 'fixture_id': self.fixture.id, 'user_id': self.comm_user2.id
        })
        resp = self.client.post(unassign_url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(FixtureCommentatorAssignment.objects.filter(
            fixture=self.fixture, user=self.comm_user2
        ).exists())

    def test_commentary_workspace_authorization_and_lifecycle(self):
        """Tests live workspace authorization, publish API, edit API, delete API and validations."""
        CommentatorProfile.objects.create(user=self.comm_user1, experience_level='PROFESSIONAL', is_available=True)
        FixtureCommentatorAssignment.objects.create(
            fixture=self.fixture, user=self.comm_user1, role='LEAD'
        )

        ws_url = reverse('fixture_commentary_workspace', kwargs={
            'slug': self.tournament.slug, 'fixture_id': self.fixture.id
        })
        pub_url = reverse('commentary_publish_api', kwargs={
            'slug': self.tournament.slug, 'fixture_id': self.fixture.id
        })

        # Unauthorized user gets redirected or 403
        self.client.force_login(self.regular_user)
        resp = self.client.get(ws_url)
        self.assertEqual(resp.status_code, 302)  # Redirects with error message
        resp = self.client.post(pub_url, {'text': 'Hello fans!'})
        self.assertEqual(resp.status_code, 403)

        # Assigned commentator gets 200 on workspace
        self.client.force_login(self.comm_user1)
        resp = self.client.get(ws_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Publish Live Commentary')
        self.assertContains(resp, 'Lakers Pro')

        # Publish commentary entry
        resp = self.client.post(pub_url, {
            'text': 'What an incredible block at the rim by Lakers Pro defense!',
            'category': 'KEY_MOMENT',
            'match_clock': 'Q3 04:12'
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        entry_id = data['entry']['id']
        self.assertEqual(data['entry']['category'], 'KEY_MOMENT')

        # Verify entry in database
        entry = CommentaryEntry.objects.get(id=entry_id)
        self.assertEqual(entry.author, self.comm_user1)
        self.assertEqual(entry.match_clock, 'Q3 04:12')
        self.assertFalse(entry.is_deleted)

        # Validation: empty text rejected
        resp = self.client.post(pub_url, {'text': '   '})
        self.assertEqual(resp.status_code, 400)

        # Validation: duplicate text rejected within short window
        resp = self.client.post(pub_url, {
            'text': 'What an incredible block at the rim by Lakers Pro defense!',
            'category': 'KEY_MOMENT'
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Duplicate', resp.json().get('error', ''))

        # Edit entry
        edit_url = reverse('commentary_edit_api', kwargs={
            'slug': self.tournament.slug, 'fixture_id': self.fixture.id, 'entry_id': entry_id
        })
        resp = self.client.post(edit_url, {
            'text': 'What an incredible block at the rim by Lakers Pro defense! (Updated)',
            'category': 'KEY_MOMENT'
        })
        self.assertEqual(resp.status_code, 200)
        entry.refresh_from_db()
        self.assertIn('(Updated)', entry.text)
        self.assertIsNotNone(entry.edited_at)

        # Delete entry
        del_url = reverse('commentary_delete_api', kwargs={
            'slug': self.tournament.slug, 'fixture_id': self.fixture.id, 'entry_id': entry_id
        })
        resp = self.client.post(del_url)
        self.assertEqual(resp.status_code, 200)
        entry.refresh_from_db()
        self.assertTrue(entry.is_deleted)

    def test_commentary_sync_api_and_score_read_only_protection(self):
        """Tests that sync API delivers entries & scores, and commentators cannot tamper with official scoring."""
        CommentatorProfile.objects.create(user=self.comm_user1, experience_level='PROFESSIONAL', is_available=True)
        FixtureCommentatorAssignment.objects.create(
            fixture=self.fixture, user=self.comm_user1, role='LEAD'
        )

        entry = CommentaryEntry.objects.create(
            fixture=self.fixture,
            author=self.comm_user1,
            text='Lakers Pro hits a 3-pointer from downtown!',
            category='MATCH_UPDATE',
            score_snapshot='42 - 39'
        )

        sync_url = reverse('commentary_sync_api', kwargs={
            'slug': self.tournament.slug, 'fixture_id': self.fixture.id
        })

        # Sync API is readable by spectators without authentication
        self.client.logout()
        resp = self.client.get(sync_url + '?since_id=0')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'LIVE')
        self.assertEqual(len(data['entries']), 1)
        self.assertEqual(data['entries'][0]['text'], 'Lakers Pro hits a 3-pointer from downtown!')
        self.assertEqual(len(data['participants']), 2)
        self.assertEqual(data['participants'][0]['score'], 42.0)

        # Security test: Commentator attempts to mutate official score directly via scoring endpoint
        self.client.force_login(self.comm_user1)
        score_url = reverse('score_fixture', kwargs={
            'slug': self.tournament.slug, 'fixture_id': self.fixture.id
        })
        # Attempting to score without organizer/referee authority
        resp = self.client.post(score_url, {
            'action': 'score_increment',
            'participant_id': self.p1.id,
            'delta': '10'
        })
        # Must be rejected (redirect to organizer_status or permission denied)
        self.assertNotEqual(resp.status_code, 200)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.score, 42)  # Score remains untouched!

    def test_public_match_detail_shows_commentary(self):
        """Public fixture page displays commentary timeline and broadcast team."""
        CommentatorProfile.objects.create(user=self.comm_user1, experience_level='PROFESSIONAL', is_available=True)
        FixtureCommentatorAssignment.objects.create(
            fixture=self.fixture, user=self.comm_user1, role='LEAD'
        )
        CommentaryEntry.objects.create(
            fixture=self.fixture,
            author=self.comm_user1,
            text='Tip-off underway in front of a packed arena!',
            category='GENERAL'
        )

        match_url = reverse('match_detail', kwargs={
            'slug': self.tournament.slug, 'pk': self.fixture.id
        })
        resp = self.client.get(match_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Live Commentary')
        self.assertContains(resp, 'Mike Voice')
        self.assertContains(resp, 'Tip-off underway in front of a packed arena!')
