from decimal import Decimal
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from accounts.models import OrganizerProfile, User
from tournaments import constants as C
from tournaments.models import (
    Fixture, FixtureParticipant, ScoreEvent, Sport, Team, Tournament, TournamentTeamEntry
)
from tournaments.pools import pool_view_context


class PoolPreservationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='org_user', email='org@example.com', password='password123')
        self.organizer = OrganizerProfile.objects.create(
            user=self.user, organization_name='Hoop Masters', is_approved=True
        )
        self.sport, _ = Sport.objects.get_or_create(slug='basketball', defaults={'name': 'Basketball', 'icon': '🏀'})
        today = timezone.now().date()
        self.tournament = Tournament.objects.create(
            name='Legends Basketball Tournament',
            slug='legends-basketball-tournament',
            sport=self.sport,
            organizer=self.organizer,
            start_date=today,
            end_date=today,
            format=C.FORMAT_KNOCKOUT,
            fixture_mode='POOL',
            status='PUBLISHED',
            pool_config={'order': ['Pool A'], 'extra_labels': []},
        )
        self.team1 = Team.objects.create(name='Lakers', sport=self.sport)
        self.team2 = Team.objects.create(name='Celtics', sport=self.sport)
        self.team3 = Team.objects.create(name='Warriors', sport=self.sport)
        self.team4 = Team.objects.create(name='Bulls', sport=self.sport)

        self.e1 = TournamentTeamEntry.objects.create(
            tournament=self.tournament, team=self.team1, status='APPROVED', group_name='Pool A', seed=1
        )
        self.e2 = TournamentTeamEntry.objects.create(
            tournament=self.tournament, team=self.team2, status='APPROVED', group_name='Pool A', seed=2
        )
        self.e3 = TournamentTeamEntry.objects.create(
            tournament=self.tournament, team=self.team3, status='APPROVED', group_name='', seed=3
        )
        self.e4 = TournamentTeamEntry.objects.create(
            tournament=self.tournament, team=self.team4, status='APPROVED', group_name='', seed=4
        )

        # Initial fixture in Pool A that was played and completed
        self.fx_pool_a = Fixture.objects.create(
            tournament=self.tournament, round_no=1, sequence=1,
            round_name='Pool A · Round 1',
            stage=C.STAGE_POOL, pool_name='Pool A', status='COMPLETED'
        )
        self.p1 = FixtureParticipant.objects.create(
            fixture=self.fx_pool_a, team=self.team1, slot=0, score=Decimal('65.00'), is_winner=True
        )
        self.p2 = FixtureParticipant.objects.create(
            fixture=self.fx_pool_a, team=self.team2, slot=1, score=Decimal('60.00'), is_winner=False
        )
        ScoreEvent.objects.create(
            fixture=self.fx_pool_a, participant=self.p1, event_type='score',
            description='Lakers +2', score_snapshot={'points': 2}
        )
        self.tournament.engine.compute_standings()

    def test_adding_teams_to_new_pool_preserves_completed_fixtures_of_previous_pool(self):
        client = Client()
        client.force_login(self.user)

        # Verify initial state: Pool A fixture exists and has score
        self.assertEqual(
            self.tournament.fixtures.filter(stage=C.STAGE_POOL, pool_name='Pool A', status='COMPLETED').count(),
            1
        )

        # Step 1: Create a new pool "Pool B" (or "Championship Pool")
        create_url = reverse('pool_create', args=[self.tournament.slug])
        resp = client.post(create_url, {'pool_name': 'Pool B'})
        self.assertEqual(resp.status_code, 302)

        # Step 2: Add teams to the new pool via pool_fixture_build
        # We select Lakers (team 1) who played in Pool A, and Warriors (team 3)
        build_url = reverse('pool_fixture_build', args=[self.tournament.slug])
        resp = client.post(build_url, {
            'pool_label': 'Pool B',
            'entrant_ids': [f'team:{self.team1.id}', f'team:{self.team3.id}'],
            'pairing_mode': 'adjacent',
        })
        self.assertEqual(resp.status_code, 302)

        # CRITICAL ASSERTION:
        # The completed fixture in Pool A must NOT have been deleted!
        pool_a_fixtures = self.tournament.fixtures.filter(stage=C.STAGE_POOL, pool_name='Pool A')
        self.assertEqual(pool_a_fixtures.count(), 1, "Pool A completed fixture was unexpectedly deleted!")
        fx_a = pool_a_fixtures.first()
        self.assertEqual(fx_a.status, 'COMPLETED')
        self.assertEqual(fx_a.participants.get(team=self.team1).score, Decimal('65.00'))
        self.assertTrue(fx_a.participants.get(team=self.team1).is_winner)
        self.assertEqual(fx_a.events.count(), 1)

        # Verify new fixtures in Pool B were created
        pool_b_fixtures = self.tournament.fixtures.filter(stage=C.STAGE_POOL, pool_name='Pool B')
        self.assertEqual(pool_b_fixtures.count(), 1)
        fx_b = pool_b_fixtures.first()
        self.assertEqual(fx_b.status, 'SCHEDULED')
        parts_b = {p.team_id for p in fx_b.participants.all()}
        self.assertEqual(parts_b, {self.team1.id, self.team3.id})

        # Verify standings for both Pool A and Pool B exist
        ctx = pool_view_context(self.tournament)
        pool_labels = [p['label'] for p in ctx['pools']]
        self.assertIn('Pool A', pool_labels)
        self.assertIn('Pool B', pool_labels)

        pool_a_data = next(p for p in ctx['pools'] if p['label'] == 'Pool A')
        self.assertEqual(pool_a_data['played'], 1)
        self.assertEqual(len(pool_a_data['standings']), 2)
        # Lakers should be 1st in Pool A with 1 win
        lakers_standing = next(s for s in pool_a_data['standings'] if s.team_id == self.team1.id)
        self.assertEqual(lakers_standing.won, 1)
        self.assertEqual(lakers_standing.points, 2.0)

    def test_rebuilding_pool_does_not_delete_its_own_completed_fixtures(self):
        # Create a completed fixture in Pool B
        fx_b1 = Fixture.objects.create(
            tournament=self.tournament, round_no=1, sequence=2,
            round_name='Pool B · Round 1',
            stage=C.STAGE_POOL, pool_name='Pool B', status='COMPLETED'
        )
        FixtureParticipant.objects.create(
            fixture=fx_b1, team=self.team1, slot=0, score=Decimal('70.00'), is_winner=True
        )
        FixtureParticipant.objects.create(
            fixture=fx_b1, team=self.team3, slot=1, score=Decimal('68.00'), is_winner=False
        )

        client = Client()
        client.force_login(self.user)
        build_url = reverse('pool_fixture_build', args=[self.tournament.slug])

        # Rebuild Pool B by adding another team (team 4)
        resp = client.post(build_url, {
            'pool_label': 'Pool B',
            'entrant_ids': [f'team:{self.team1.id}', f'team:{self.team3.id}', f'team:{self.team4.id}'],
            'pairing_mode': 'adjacent',
        })
        self.assertEqual(resp.status_code, 302)

        # fx_b1 should still be COMPLETED and not deleted
        self.assertTrue(Fixture.objects.filter(id=fx_b1.id, status='COMPLETED').exists())
        # Pool A should still be intact
        self.assertTrue(Fixture.objects.filter(id=self.fx_pool_a.id, status='COMPLETED').exists())

    def test_delete_pool_removes_all_matches_scores_and_standings(self):
        client = Client()
        client.force_login(self.user)

        # Create Pool B with unplayed and played fixtures
        fx_b = Fixture.objects.create(
            tournament=self.tournament, round_no=1, sequence=2,
            round_name='Pool B · Round 1',
            stage=C.STAGE_POOL, pool_name='Pool B', status='SCHEDULED'
        )
        p_b1 = FixtureParticipant.objects.create(fixture=fx_b, team=self.team3, slot=0)
        p_b2 = FixtureParticipant.objects.create(fixture=fx_b, team=self.team4, slot=1)
        self.e3.group_name = 'Pool B'
        self.e3.save()
        self.e4.group_name = 'Pool B'
        self.e4.save()

        cfg = dict(self.tournament.pool_config or {})
        cfg['order'] = ['Pool A', 'Pool B']
        self.tournament.pool_config = cfg
        self.tournament.save()
        self.tournament.engine.compute_standings()

        self.assertTrue(Fixture.objects.filter(id=fx_b.id).exists())
        self.assertTrue(self.tournament.standings.filter(group_name='Pool B').exists())

        # Post to delete Pool B
        delete_url = reverse('pool_delete', args=[self.tournament.slug])
        resp = client.post(delete_url, {'pool_name': 'Pool B'})
        self.assertEqual(resp.status_code, 302)

        # Pool B fixtures should be completely deleted
        self.assertFalse(Fixture.objects.filter(id=fx_b.id).exists())
        self.assertFalse(Fixture.objects.filter(stage=C.STAGE_POOL, pool_name='Pool B').exists())
        # Pool B standings should be deleted
        self.assertFalse(self.tournament.standings.filter(group_name='Pool B').exists())
        # Entrant group names should be cleared
        self.e3.refresh_from_db()
        self.e4.refresh_from_db()
        self.assertEqual(self.e3.group_name, '')
        self.assertEqual(self.e4.group_name, '')
        # Pool B removed from pool_config order
        self.tournament.refresh_from_db()
        self.assertNotIn('Pool B', self.tournament.pool_config.get('order', []))
        # Pool A fixtures and completed match data should be completely intact
        self.assertTrue(Fixture.objects.filter(id=self.fx_pool_a.id, status='COMPLETED').exists())

    def test_non_organizer_cannot_delete_pool(self):
        other_user = User.objects.create_user(username='other_user', email='other@example.com', password='password123')
        client = Client()
        client.force_login(other_user)

        delete_url = reverse('pool_delete', args=[self.tournament.slug])
        resp = client.post(delete_url, {'pool_name': 'Pool A'})
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('organizer_status'))
        # Pool A still intact
        self.assertTrue(Fixture.objects.filter(id=self.fx_pool_a.id).exists())
