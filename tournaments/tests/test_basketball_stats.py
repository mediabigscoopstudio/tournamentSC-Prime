from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, OrganizerProfile
from tournaments import constants as C
from tournaments.models import (
    Fixture, FixtureParticipant, ScoreEvent, Sport, Team, TeamMembership, Tournament
)
from tournaments.views import _basketball_scoring_context


class BasketballStatsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='coach_bob', email='bob@example.com', password='password123')
        self.organizer = OrganizerProfile.objects.create(user=self.user, organization_name='Hoop League')
        self.sport, _ = Sport.objects.get_or_create(slug='basketball', defaults={'name': 'Basketball', 'icon': '🏀'})
        today = timezone.now().date()
        self.tournament = Tournament.objects.create(
            name='Summer Basketball Classic',
            slug='summer-basketball-classic',
            sport=self.sport,
            organizer=self.organizer,
            start_date=today,
            end_date=today,
            format=C.FORMAT_KNOCKOUT,
            fixture_mode='POOL',
            status='PUBLISHED',
        )
        self.team_a = Team.objects.create(name='Lakers', sport=self.sport)
        self.team_b = Team.objects.create(name='Celtics', sport=self.sport)

        self.player_a1 = TeamMembership.objects.create(
            team=self.team_a, display_name='Kobe Bryant', jersey_number='24', is_approved=True
        )
        self.player_a2 = TeamMembership.objects.create(
            team=self.team_a, display_name='Shaquille ONeal', jersey_number='34', is_approved=True
        )
        self.player_b1 = TeamMembership.objects.create(
            team=self.team_b, display_name='Larry Bird', jersey_number='33', is_approved=True
        )

        self.fixture = Fixture.objects.create(
            tournament=self.tournament,
            round_no=1,
            sequence=1,
            round_name='Championship Game',
            stage=C.STAGE_KNOCKOUT,
            status='LIVE',
            individual_scoring_enabled=True,
        )
        self.part_a = FixtureParticipant.objects.create(
            fixture=self.fixture, team=self.team_a, slot=0, score=Decimal('0.00')
        )
        self.part_b = FixtureParticipant.objects.create(
            fixture=self.fixture, team=self.team_b, slot=1, score=Decimal('0.00')
        )

    def test_record_assist_rebound_steal_and_free_throw(self):
        client = Client()
        client.force_login(self.user)
        url = reverse('score_fixture', args=[self.tournament.slug, self.fixture.id])

        # Record Free Throw for Kobe (+1 PT and FT)
        resp = client.post(url, {
            'action': 'record_stat',
            'stat_type': 'free_throw',
            'participant_id': self.part_a.id,
            'membership_id': self.player_a1.id,
        })
        self.assertEqual(resp.status_code, 302)
        self.part_a.refresh_from_db()
        self.assertEqual(self.part_a.score, Decimal('1.00'))

        # Record Rebound for Shaq
        resp = client.post(url, {
            'action': 'record_stat',
            'stat_type': 'rebound',
            'participant_id': self.part_a.id,
            'membership_id': self.player_a2.id,
        })
        self.assertEqual(resp.status_code, 302)

        # Record Assist for Kobe
        resp = client.post(url, {
            'action': 'record_stat',
            'stat_type': 'assist',
            'participant_id': self.part_a.id,
            'membership_id': self.player_a1.id,
        })
        self.assertEqual(resp.status_code, 302)

        # Record Steal for Kobe
        resp = client.post(url, {
            'action': 'record_stat',
            'stat_type': 'steal',
            'participant_id': self.part_a.id,
            'membership_id': self.player_a1.id,
        })
        self.assertEqual(resp.status_code, 302)

        # Verify _basketball_scoring_context
        _, individual_rows, rows_by_team = _basketball_scoring_context(self.fixture)
        kobe_row = next(r for r in rows_by_team[self.part_a.id] if r['name'] == 'Kobe Bryant')
        shaq_row = next(r for r in rows_by_team[self.part_a.id] if r['name'] == 'Shaquille ONeal')

        self.assertEqual(kobe_row['total'], 1)
        self.assertEqual(kobe_row['ft'], 1)
        self.assertEqual(kobe_row['ast'], 1)
        self.assertEqual(kobe_row['stl'], 1)
        self.assertEqual(shaq_row['reb'], 1)

    def test_public_match_detail_shows_stats(self):
        # Create events
        ScoreEvent.objects.create(
            fixture=self.fixture,
            participant=self.part_a,
            event_type='score',
            description='Lakers +1 FT — Kobe Bryant (#24)',
            score_snapshot={'membership_id': self.player_a1.id, 'player_name': 'Kobe Bryant',
                            'jersey_number': '24', 'points': 1, 'is_ft': True,
                            'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture,
            participant=self.part_a,
            event_type='rebound',
            description='Rebound — Shaquille ONeal (#34)',
            score_snapshot={'membership_id': self.player_a2.id, 'player_name': 'Shaquille ONeal',
                            'jersey_number': '34', 'stat': 'rebound',
                            'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture,
            participant=self.part_a,
            event_type='assist',
            description='Assist — Kobe Bryant (#24)',
            score_snapshot={'membership_id': self.player_a1.id, 'player_name': 'Kobe Bryant',
                            'jersey_number': '24', 'stat': 'assist',
                            'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture,
            participant=self.part_a,
            event_type='steal',
            description='Steal — Kobe Bryant (#24)',
            score_snapshot={'membership_id': self.player_a1.id, 'player_name': 'Kobe Bryant',
                            'jersey_number': '24', 'stat': 'steal',
                            'team_participant_id': self.part_a.id}
        )

        ScoreEvent.objects.create(
            fixture=self.fixture,
            participant=self.part_b,
            event_type='score',
            description='Celtics +2 — Larry Bird (#33)',
            score_snapshot={'membership_id': self.player_b1.id, 'player_name': 'Larry Bird',
                            'jersey_number': '33', 'points': 2,
                            'team_participant_id': self.part_b.id}
        )
        self.part_b.score = Decimal('2.00')
        self.part_b.save()
        self.part_a.score = Decimal('1.00')
        self.part_a.save()

        client = Client()
        url = reverse('match_detail', args=[self.tournament.slug, self.fixture.id])
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode('utf-8')
        # Check column headers in scorecard
        self.assertIn('>FT<', content)
        self.assertIn('>REB<', content)
        self.assertIn('>AST<', content)
        self.assertIn('>STL<', content)
        # Check player names
        self.assertIn('Kobe Bryant', content)
        self.assertIn('Shaquille ONeal', content)
        self.assertIn('Larry Bird', content)
        # Check Match Stats section has Steals and both team bars
        self.assertIn('Steals', content)
        # Verify right-side team bar has display:flex and background #0f172a with height 100%
        self.assertIn('justify-content:flex-start;', content)
        self.assertIn('background:#0f172a;', content)
        self.assertIn('height:100%;', content)
        # Check Match Stats section has Steals
        self.assertIn('Steals', content)

    def test_undo_stat_action(self):
        client = Client()
        client.force_login(self.user)
        url = reverse('score_fixture', args=[self.tournament.slug, self.fixture.id])

        # Record a steal
        client.post(url, {
            'action': 'record_stat',
            'stat_type': 'steal',
            'participant_id': self.part_a.id,
            'membership_id': self.player_a1.id,
        })
        self.assertEqual(self.fixture.events.filter(event_type='steal').count(), 1)

        # Undo last action
        resp = client.post(url, {'action': 'undo_last_score'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.fixture.events.filter(event_type='steal').count(), 0)

    def test_compute_basketball_team_stats_and_live_sync(self):
        from tournaments.stats import compute_basketball_team_stats
        from tournaments.views import build_fixture_live_payload

        # Team A: 1 free throw (1 pt), 1 two-pointer (2 pts), 1 three-pointer (3 pts) => 6 pts, 2 FG, 1/1 FT
        # Also 2 rebounds, 1 assist, 1 steal, 2 fouls
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_a, event_type='score',
            description='Lakers +1 FT',
            score_snapshot={'membership_id': self.player_a1.id, 'points': 1, 'is_ft': True,
                            'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_a, event_type='score',
            description='Lakers +2',
            score_snapshot={'membership_id': self.player_a1.id, 'points': '2',
                            'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_a, event_type='score',
            description='Lakers +3',
            score_snapshot={'membership_id': self.player_a1.id, 'points': 3,
                            'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_a, event_type='rebound',
            description='Rebound',
            score_snapshot={'membership_id': self.player_a2.id, 'stat': 'rebound',
                            'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_a, event_type='rebound',
            description='Rebound',
            score_snapshot={'membership_id': self.player_a2.id, 'stat': 'rebound',
                            'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_a, event_type='assist',
            description='Assist',
            score_snapshot={'membership_id': self.player_a1.id, 'stat': 'assist',
                            'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_a, event_type='steal',
            description='Steal',
            score_snapshot={'membership_id': self.player_a1.id, 'stat': 'steal',
                            'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_a, event_type='foul',
            description='Foul',
            score_snapshot={'membership_id': self.player_a1.id, 'team_participant_id': self.part_a.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_a, event_type='foul',
            description='Foul',
            score_snapshot={'membership_id': self.player_a2.id, 'team_participant_id': self.part_a.id}
        )
        self.part_a.score = Decimal('6.00')
        self.part_a.fouls = 2
        self.part_a.save()

        # Team B: 1 two-pointer (2 pts) => 2 pts, 1 FG, 0/0 FT, 1 rebound, 0 assist, 0 steal, 1 foul
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_b, event_type='score',
            description='Celtics +2',
            score_snapshot={'membership_id': self.player_b1.id, 'points': 2,
                            'team_participant_id': self.part_b.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_b, event_type='rebound',
            description='Rebound',
            score_snapshot={'membership_id': self.player_b1.id, 'stat': 'rebound',
                            'team_participant_id': self.part_b.id}
        )
        ScoreEvent.objects.create(
            fixture=self.fixture, participant=self.part_b, event_type='foul',
            description='Foul',
            score_snapshot={'membership_id': self.player_b1.id, 'team_participant_id': self.part_b.id}
        )
        self.part_b.score = Decimal('2.00')
        self.part_b.fouls = 1
        self.part_b.save()

        stats = compute_basketball_team_stats(self.fixture)
        self.assertIsNotNone(stats)
        self.assertEqual(stats['team_a']['points'], 6)
        self.assertEqual(stats['team_a']['field_goals'], 2)
        self.assertEqual(stats['team_a']['free_throws_made'], 1)
        self.assertEqual(stats['team_a']['free_throws_att'], 1)
        self.assertEqual(stats['team_a']['rebounds'], 2)
        self.assertEqual(stats['team_a']['assists'], 1)
        self.assertEqual(stats['team_a']['steals'], 1)
        self.assertEqual(stats['team_a']['fouls'], 2)

        self.assertEqual(stats['team_b']['points'], 2)
        self.assertEqual(stats['team_b']['field_goals'], 1)
        self.assertEqual(stats['team_b']['free_throws_made'], 0)
        self.assertEqual(stats['team_b']['free_throws_att'], 0)
        self.assertEqual(stats['team_b']['rebounds'], 1)
        self.assertEqual(stats['team_b']['assists'], 0)
        self.assertEqual(stats['team_b']['steals'], 0)
        self.assertEqual(stats['team_b']['fouls'], 1)

        # Check percentages
        self.assertEqual(stats['team_a']['points_pct'], 75)
        self.assertEqual(stats['team_b']['points_pct'], 25)

        # Check build_fixture_live_payload includes team_stats
        payload = build_fixture_live_payload(self.fixture)
        self.assertIn('team_stats', payload)
        self.assertEqual(payload['team_stats']['team_a']['points'], 6)

        # Check commentary_sync_api returns team_stats
        client = Client()
        sync_url = reverse('commentary_sync_api', args=[self.tournament.slug, self.fixture.id])
        resp = client.get(sync_url)
        self.assertEqual(resp.status_code, 200)
        json_data = resp.json()
        self.assertIn('team_stats', json_data)
        self.assertEqual(json_data['team_stats']['team_a']['points'], 6)
        self.assertEqual(json_data['team_stats']['team_a']['field_goals'], 2)

    def test_adjust_score_syncs_score_event(self):
        client = Client()
        client.force_login(self.user)
        url = reverse('score_fixture', args=[self.tournament.slug, self.fixture.id])

        # Score a +2
        client.post(url, {
            'action': 'score_point',
            'points': '2',
            'participant_id': self.part_a.id,
            'membership_id': self.player_a1.id,
        })
        self.part_a.refresh_from_db()
        self.assertEqual(self.part_a.score, Decimal('2.00'))
        self.assertEqual(self.fixture.events.filter(participant=self.part_a, event_type='score').count(), 1)

        # Adjust score (decrement by 1)
        client.post(url, {
            'action': 'adjust_score',
            'participant_id': self.part_a.id,
        })
        self.part_a.refresh_from_db()
        self.assertEqual(self.part_a.score, Decimal('1.00'))
        ev = self.fixture.events.filter(participant=self.part_a, event_type='score').first()
        self.assertIsNotNone(ev)
        self.assertEqual(ev.score_snapshot['points'], 1)

        # Adjust score again (decrement to 0) -> event should be deleted
        client.post(url, {
            'action': 'adjust_score',
            'participant_id': self.part_a.id,
        })
        self.part_a.refresh_from_db()
        self.assertEqual(self.part_a.score, Decimal('0.00'))
        self.assertEqual(self.fixture.events.filter(participant=self.part_a, event_type='score').count(), 0)
