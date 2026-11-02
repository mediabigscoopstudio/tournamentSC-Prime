from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, OrganizerProfile
from tournaments import constants as C
from tournaments.models import Fixture, FixtureParticipant, Sport, Team, Tournament
from tournaments.pools import pool_view_context


class ChampionDisplayTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='org_user', email='org@example.com', password='password123')
        self.organizer = OrganizerProfile.objects.create(user=self.user, organization_name='Org')
        self.sport = Sport.objects.create(name='Basketball', slug='basketball', icon='🏀')
        today = timezone.now().date()
        self.tournament = Tournament.objects.create(
            name='Test Championship',
            slug='test-championship',
            sport=self.sport,
            organizer=self.organizer,
            start_date=today,
            end_date=today,
            format=C.FORMAT_KNOCKOUT,
            fixture_mode='POOL',
            status='PUBLISHED',
        )
        self.team1 = Team.objects.create(name='Alpha Titans', sport=self.sport)
        self.team2 = Team.objects.create(name='Beta Warriors', sport=self.sport)

    def test_pool_view_context_champion_resolved(self):
        # Create a final knockout fixture
        fx = Fixture.objects.create(
            tournament=self.tournament,
            round_no=3,
            sequence=1,
            round_name='Final',
            stage=C.STAGE_KNOCKOUT,
            status='COMPLETED',
        )
        p1 = FixtureParticipant.objects.create(fixture=fx, team=self.team1, score=Decimal('85.00'), is_winner=True)
        p2 = FixtureParticipant.objects.create(fixture=fx, team=self.team2, score=Decimal('78.00'), is_winner=False)

        ctx = pool_view_context(self.tournament)
        self.assertIsNotNone(ctx['champion'])
        self.assertEqual(ctx['champion'].name, 'Alpha Titans')
        self.assertEqual(ctx['runner_up'].name, 'Beta Warriors')
        self.assertEqual(ctx['final_fixture'].id, fx.id)

    def test_standings_and_match_views_render_champion(self):
        fx = Fixture.objects.create(
            tournament=self.tournament,
            round_no=3,
            sequence=1,
            round_name='Final',
            stage=C.STAGE_KNOCKOUT,
            status='COMPLETED',
        )
        FixtureParticipant.objects.create(fixture=fx, team=self.team1, score=Decimal('85.00'), is_winner=True)
        FixtureParticipant.objects.create(fixture=fx, team=self.team2, score=Decimal('78.00'), is_winner=False)

        client = Client()

        # Check match detail view
        resp_match = client.get(reverse('match_detail', args=[self.tournament.slug, fx.id]))
        self.assertEqual(resp_match.status_code, 200)
        self.assertContains(resp_match, 'CHAMPION')
        self.assertContains(resp_match, 'won the Final and is crowned')

        # Check standings view
        resp_standings = client.get(reverse('tournament_standings', args=[self.tournament.slug]))
        self.assertEqual(resp_standings.status_code, 200)
        self.assertContains(resp_standings, 'Tournament Champion')
        self.assertContains(resp_standings, 'Alpha Titans')
        self.assertContains(resp_standings, 'View Final Match')
