import hashlib
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import RefereeProfile, OrganizerProfile
from tournaments import constants as C
from tournaments.models import (
    Fixture,
    FixtureParticipant,
    FixtureRefereeAssignment,
    MatchAuditLog,
    Sport,
    Tournament,
    TournamentCoOrganizer,
    TournamentCoOrganizerInvitation,
    TournamentRefereeRegistration,
)

User = get_user_model()


class RefereeAndCoOrganizerTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Create Sport
        self.sport = Sport.objects.create(name='Basketball', slug='basketball', format_type='TEAM')

        # Create Organizer User & Profile
        self.organizer_user = User.objects.create_user(
            username='organizer1',
            email='organizer1@example.com',
            password='password123',
            first_name='Lead',
            last_name='Organizer'
        )
        self.organizer_profile = OrganizerProfile.objects.create(
            user=self.organizer_user,
            organization_name='Acme Sports League',
            is_approved=True
        )

        # Create Candidate Referee Users
        self.ref_user1 = User.objects.create_user(
            username='referee_john',
            email='ref_john@example.com',
            password='password123',
            first_name='John',
            last_name='Whistle'
        )
        self.ref_user2 = User.objects.create_user(
            username='referee_sarah',
            email='ref_sarah@example.com',
            password='password123',
            first_name='Sarah',
            last_name='Courts'
        )

        # Create Regular User (Future Co-Organizer)
        self.co_org_user = User.objects.create_user(
            username='co_organizer_alex',
            email='alex@example.com',
            password='password123',
            first_name='Alex',
            last_name='Manager'
        )

        # Create Regular Player (Unrelated)
        self.player_user = User.objects.create_user(
            username='player_bob',
            email='bob@example.com',
            password='password123',
            first_name='Bob',
            last_name='Player'
        )

        # Create Tournament
        self.tournament = Tournament.objects.create(
            organizer=self.organizer_profile,
            sport=self.sport,
            name='Summer Basketball Championship',
            slug='summer-basketball-championship',
            format=C.FORMAT_KNOCKOUT,
            status='PUBLISHED',
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=5)).date()
        )

        # Create 2 Fixtures
        self.fixture1 = Fixture.objects.create(
            tournament=self.tournament,
            round_no=1,
            round_name='Quarterfinal 1',
            status='SCHEDULED',
            scheduled_time=timezone.now() + timedelta(days=1)
        )
        self.p1_1 = FixtureParticipant.objects.create(fixture=self.fixture1, slot=0, label='Team Alpha')
        self.p1_2 = FixtureParticipant.objects.create(fixture=self.fixture1, slot=1, label='Team Beta')

        self.fixture2 = Fixture.objects.create(
            tournament=self.tournament,
            round_no=1,
            round_name='Quarterfinal 2',
            status='SCHEDULED',
            scheduled_time=timezone.now() + timedelta(days=1, hours=2)
        )
        self.p2_1 = FixtureParticipant.objects.create(fixture=self.fixture2, slot=0, label='Team Gamma')
        self.p2_2 = FixtureParticipant.objects.create(fixture=self.fixture2, slot=1, label='Team Delta')

    # =========================================================================
    # 1. REFEREE PROFILE & ONBOARDING
    # =========================================================================
    def test_referee_onboarding_and_profile_creation(self):
        self.client.force_login(self.ref_user1)

        # Check navbar property before onboarding
        self.assertFalse(self.ref_user1.has_referee_profile)

        # GET onboarding page
        response = self.client.get(reverse('referee_onboarding'))
        self.assertEqual(response.status_code, 200)

        # POST onboarding form
        post_data = {
            'first_name': 'John Whistle',
            'experience_level': 'REGIONAL',
            'years_experience': 4,
            'preferred_location': 'Metropolis Arena',
            'certifications': 'FIBA Level 2 Licensed Referee',
            'bio': 'Passionate about fair play and fast-paced game management.',
            'is_available': True,
            'sports': [self.sport.id],
        }
        res = self.client.post(reverse('referee_onboarding'), post_data)
        self.assertRedirects(res, reverse('referee_dashboard'))

        # User now has referee profile
        self.ref_user1.refresh_from_db()
        self.assertTrue(self.ref_user1.has_referee_profile)
        ref_profile = self.ref_user1.referee_profile
        self.assertEqual(ref_profile.experience_level, 'REGIONAL')
        self.assertEqual(ref_profile.years_experience, 4)
        self.assertTrue(ref_profile.is_available)

        # Access Referee Dashboard
        dash_res = self.client.get(reverse('referee_dashboard'))
        self.assertEqual(dash_res.status_code, 200)

        # Access Public Referee Profile
        pub_res = self.client.get(reverse('referee_public', kwargs={'pk': ref_profile.pk}))
        self.assertEqual(pub_res.status_code, 200)
        self.assertContains(pub_res, 'John Whistle')
        self.assertContains(pub_res, 'FIBA Level 2')

        # Edit Profile
        edit_data = post_data.copy()
        edit_data['years_experience'] = 5
        edit_res = self.client.post(reverse('referee_profile_edit'), edit_data)
        self.assertRedirects(edit_res, reverse('referee_dashboard'))
        ref_profile.refresh_from_db()
        self.assertEqual(ref_profile.years_experience, 5)

    def test_referee_dashboard_redirects_unonboarded_user(self):
        self.client.force_login(self.player_user)
        res = self.client.get(reverse('referee_dashboard'))
        self.assertRedirects(res, reverse('referee_onboarding'))

    # =========================================================================
    # 2. TOURNAMENT REFEREE MANAGEMENT (POOL, AUTO-ASSIGN & MANUAL ASSIGN)
    # =========================================================================
    def test_tournament_referee_pool_and_assignment_flows(self):
        # Create RefereeProfiles for both referees
        RefereeProfile.objects.create(
            user=self.ref_user1, experience_level='NATIONAL', years_experience=6, is_available=True
        )
        RefereeProfile.objects.create(
            user=self.ref_user2, experience_level='REGIONAL', years_experience=3, is_available=True
        )

        self.client.force_login(self.organizer_user)

        # 1. Search API
        search_res = self.client.get(reverse('tournament_referees_search_api', kwargs={'slug': self.tournament.slug}) + '?q=referee')
        self.assertEqual(search_res.status_code, 200)
        data = search_res.json()
        usernames = [r['username'] for r in data['results']]
        self.assertIn('referee_john', usernames)
        self.assertIn('referee_sarah', usernames)

        # 2. Add referee to tournament pool
        add_res = self.client.post(reverse('tournament_referee_add', kwargs={'slug': self.tournament.slug}), {
            'username': 'referee_john'
        })
        self.assertRedirects(add_res, reverse('fixtures_manage', kwargs={'slug': self.tournament.slug}))
        self.assertTrue(TournamentRefereeRegistration.objects.filter(tournament=self.tournament, user=self.ref_user1, is_active=True).exists())

        # Add second referee to pool
        self.client.post(reverse('tournament_referee_add', kwargs={'slug': self.tournament.slug}), {
            'user_id': self.ref_user2.id
        })
        self.assertEqual(self.tournament.registered_referees.filter(is_active=True).count(), 2)

        # 3. Option A: Auto-assign referees across fixtures (balanced distribution)
        auto_res = self.client.post(reverse('tournament_referees_auto_assign', kwargs={'slug': self.tournament.slug}), {
            'role': 'PRIMARY'
        })
        self.assertRedirects(auto_res, reverse('fixtures_manage', kwargs={'slug': self.tournament.slug}))

        # Verify both fixtures have distinct referees assigned
        self.assertEqual(FixtureRefereeAssignment.objects.filter(fixture=self.fixture1).count(), 1)
        self.assertEqual(FixtureRefereeAssignment.objects.filter(fixture=self.fixture2).count(), 1)

        f1_ref = FixtureRefereeAssignment.objects.get(fixture=self.fixture1).user
        f2_ref = FixtureRefereeAssignment.objects.get(fixture=self.fixture2).user
        self.assertNotEqual(f1_ref, f2_ref, "Referees should be distributed evenly across fixtures")

        # 4. Option B: Manual assign referee with role
        man_res = self.client.post(reverse('fixture_referee_assign', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}), {
            'user_id': self.ref_user2.id,
            'role': 'ASSISTANT',
            'notes': 'Assistant referee line judge'
        })
        self.assertRedirects(man_res, reverse('fixtures_manage', kwargs={'slug': self.tournament.slug}))
        self.assertTrue(FixtureRefereeAssignment.objects.filter(fixture=self.fixture1, user=self.ref_user2, role='ASSISTANT').exists())

        # 5. Manual unassign referee
        unassign_res = self.client.post(reverse('fixture_referee_unassign', kwargs={
            'slug': self.tournament.slug,
            'fixture_id': self.fixture1.id,
            'user_id': self.ref_user2.id
        }))
        self.assertRedirects(unassign_res, reverse('fixtures_manage', kwargs={'slug': self.tournament.slug}))
        self.assertFalse(FixtureRefereeAssignment.objects.filter(fixture=self.fixture1, user=self.ref_user2).exists())

        # 6. Remove referee from tournament pool
        rem_res = self.client.post(reverse('tournament_referee_remove', kwargs={
            'slug': self.tournament.slug,
            'user_id': self.ref_user1.id
        }))
        self.assertRedirects(rem_res, reverse('fixtures_manage', kwargs={'slug': self.tournament.slug}))
        self.assertFalse(self.tournament.registered_referees.filter(user=self.ref_user1, is_active=True).exists())

    # =========================================================================
    # 3. CO-ORGANIZER INVITATIONS, ACCEPTANCE & PERMISSIONS
    # =========================================================================
    def test_co_organizer_invitation_acceptance_and_management(self):
        self.client.force_login(self.organizer_user)

        # Send Invitation
        invite_res = self.client.post(reverse('tournament_co_organizer_invite', kwargs={'slug': self.tournament.slug}), {
            'username': 'co_organizer_alex'
        })
        self.assertRedirects(invite_res, reverse('tournament_manage', kwargs={'slug': self.tournament.slug}))

        # Verify invitation created with token hash
        invitation = TournamentCoOrganizerInvitation.objects.get(tournament=self.tournament, user=self.co_org_user)
        self.assertEqual(invitation.status, 'PENDING')
        self.assertEqual(len(invitation.token_hash), 64)

        # Login as recipient Alex
        self.client.force_login(self.co_org_user)

        # Try accessing tournament_manage BEFORE accepting (should be forbidden)
        before_res = self.client.get(reverse('tournament_manage', kwargs={'slug': self.tournament.slug}))
        self.assertIn(before_res.status_code, [302, 403])

        # Accept Invitation (generate token matching hash)
        raw_test_token = 'valid_test_secret_token_123456789'
        invitation.token_hash = hashlib.sha256(raw_test_token.encode('utf-8')).hexdigest()
        invitation.save()

        accept_res = self.client.get(reverse('co_organizer_accept', kwargs={'token': raw_test_token}))
        self.assertRedirects(accept_res, reverse('tournament_manage', kwargs={'slug': self.tournament.slug}))

        # Verify status is accepted and active delegation exists
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, 'ACCEPTED')
        self.assertTrue(TournamentCoOrganizer.objects.filter(tournament=self.tournament, user=self.co_org_user, is_active=True).exists())

        # Alex can now manage this tournament!
        manage_res = self.client.get(reverse('tournament_manage', kwargs={'slug': self.tournament.slug}))
        self.assertEqual(manage_res.status_code, 200)

        # Alex sees Manage button on public tournament detail page
        pub_detail_res = self.client.get(reverse('tournament_detail', kwargs={'slug': self.tournament.slug}))
        self.assertEqual(pub_detail_res.status_code, 200)
        self.assertContains(pub_detail_res, 'Manage as Co-Organizer')

        # Alex CANNOT manage other tournaments or create tournaments globally
        other_t = Tournament.objects.create(
            organizer=self.organizer_profile,
            sport=self.sport,
            name='Other Unrelated Cup',
            slug='other-unrelated-cup',
            format=C.FORMAT_KNOCKOUT,
            status='PUBLISHED',
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=5)).date()
        )
        other_manage_res = self.client.get(reverse('tournament_manage', kwargs={'slug': other_t.slug}))
        self.assertIn(other_manage_res.status_code, [302, 403])

        # Revoke access
        self.client.force_login(self.organizer_user)
        revoke_res = self.client.post(reverse('tournament_co_organizer_revoke', kwargs={
            'slug': self.tournament.slug,
            'user_id': self.co_org_user.id
        }))
        self.assertRedirects(revoke_res, reverse('tournament_manage', kwargs={'slug': self.tournament.slug}))
        self.assertFalse(TournamentCoOrganizer.objects.filter(tournament=self.tournament, user=self.co_org_user, is_active=True).exists())

    # =========================================================================
    # 4. MATCH CONTROL, REFEREE PERMISSIONS & RESULT INTEGRITY (AUDIT LOGS)
    # =========================================================================
    def test_referee_match_control_and_audit_logging(self):
        RefereeProfile.objects.create(user=self.ref_user1, is_available=True)
        # Assign ref_user1 to fixture1
        FixtureRefereeAssignment.objects.create(fixture=self.fixture1, user=self.ref_user1, role='PRIMARY')

        # 1. Unrelated user (bob) cannot access match controls
        self.client.force_login(self.player_user)
        unauth_res = self.client.get(reverse('score_fixture', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}))
        self.assertEqual(unauth_res.status_code, 403)

        # 2. Assigned Referee (John) CAN access match controls
        self.client.force_login(self.ref_user1)
        ref_res = self.client.get(reverse('score_fixture', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}))
        self.assertEqual(ref_res.status_code, 200)

        # 3. Referee starts the match
        start_res = self.client.post(reverse('score_fixture', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}), {
            'action': 'start',
            'quarter_minutes': '10',
            'shot_clock_seconds': '24'
        })
        self.assertRedirects(start_res, reverse('score_fixture', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}) + '?setup=1')
        self.fixture1.refresh_from_db()
        self.assertEqual(self.fixture1.status, 'LIVE')

        # Check MATCH_START audit log
        start_audit = MatchAuditLog.objects.filter(fixture=self.fixture1, action='MATCH_START').first()
        self.assertIsNotNone(start_audit)
        self.assertEqual(start_audit.actor, self.ref_user1)

        # 4. Referee scores and finalizes the match
        fin_res = self.client.post(reverse('score_fixture', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}), {
            'action': 'finalize',
            f'score_{self.p1_1.id}': '84',
            f'score_{self.p1_2.id}': '78',
        })
        self.assertRedirects(fin_res, reverse('score_fixture', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}))
        self.fixture1.refresh_from_db()
        self.assertEqual(self.fixture1.status, 'COMPLETED')

        # Check RESULT_DECLARED audit log
        decl_audit = MatchAuditLog.objects.filter(fixture=self.fixture1, action='RESULT_DECLARED').first()
        self.assertIsNotNone(decl_audit)

        # 5. Once COMPLETED: Referee is locked into READ-ONLY
        locked_post_res = self.client.post(reverse('score_fixture', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}), {
            'action': 'save',
            f'score_{self.p1_1.id}': '90',
            f'score_{self.p1_2.id}': '78',
        })
        self.assertRedirects(locked_post_res, reverse('score_fixture', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}))
        # Score must NOT have changed
        self.p1_1.refresh_from_db()
        self.assertEqual(self.p1_1.score, 84, "Referee should not be able to change completed match results")

        # 6. Only Organizer can submit a result correction, with mandatory reason logged to MatchAuditLog
        self.client.force_login(self.organizer_user)
        correction_res = self.client.post(reverse('score_fixture', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}), {
            'action': 'save',
            'correction_reason': 'Official table recount verified final basket',
            f'score_{self.p1_1.id}': '86',
            f'score_{self.p1_2.id}': '78',
        })
        self.assertRedirects(correction_res, reverse('score_fixture', kwargs={'slug': self.tournament.slug, 'fixture_id': self.fixture1.id}))
        self.p1_1.refresh_from_db()
        self.assertEqual(self.p1_1.score, 86)

        # Verify RESULT_CORRECTED audit log
        corr_audit = MatchAuditLog.objects.filter(fixture=self.fixture1, action='RESULT_CORRECTED').first()
        self.assertIsNotNone(corr_audit)
        self.assertEqual(corr_audit.actor, self.organizer_user)
        self.assertEqual(corr_audit.reason, 'Official table recount verified final basket')
