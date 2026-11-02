from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, OrganizerProfile
from tournaments import constants as C
from tournaments.models import (
    Fixture, FixtureParticipant, Sport, Tournament
)


class SportSpecificMatchDetailTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='org_alice', email='alice@example.com', password='password123')
        self.organizer = OrganizerProfile.objects.create(user=self.user, organization_name='Global Games')
        self.client = Client()
        today = timezone.now().date()

        # 1. Chess
        self.sport_chess, _ = Sport.objects.get_or_create(
            slug='chess', defaults={'name': 'Chess', 'icon': '♟️', 'format_type': 'INDIVIDUAL', 'default_format': C.FORMAT_ROUND_ROBIN}
        )
        self.chess_tourney = Tournament.objects.create(
            name='Test Chess Open',
            slug='test-chess-open',
            sport=self.sport_chess,
            organizer=self.organizer,
            start_date=today,
            end_date=today,
            format=C.FORMAT_ROUND_ROBIN,
            status='PUBLISHED',
        )

        # 2. Pickleball
        self.sport_pickleball, _ = Sport.objects.get_or_create(
            slug='pickleball', defaults={'name': 'Pickleball', 'icon': '🥒', 'format_type': 'BOTH', 'default_format': C.FORMAT_KNOCKOUT}
        )
        self.pickleball_tourney = Tournament.objects.create(
            name='Summer Pickleball Slam',
            slug='summer-pickleball-slam',
            sport=self.sport_pickleball,
            organizer=self.organizer,
            start_date=today,
            end_date=today,
            format=C.FORMAT_KNOCKOUT,
            status='PUBLISHED',
        )

    def test_chess_match_detail_shows_white_and_black_and_draw(self):
        # Draw game
        fx = Fixture.objects.create(
            tournament=self.chess_tourney,
            round_no=1,
            sequence=1,
            round_name='Round 1',
            status='COMPLETED',
        )
        p0 = FixtureParticipant.objects.create(
            fixture=fx, label='Magnus Carlsen', slot=0, score=Decimal('0.50'), stats={'rating': 2850}
        )
        p1 = FixtureParticipant.objects.create(
            fixture=fx, label='Hikaru Nakamura', slot=1, score=Decimal('0.50'), stats={'rating': 2820}
        )

        url = reverse('match_detail', kwargs={'slug': self.chess_tourney.slug, 'pk': fx.pk})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        # Content checks
        content = res.content.decode('utf-8')
        # Check white and black labels
        self.assertIn('⚪ White', content)
        self.assertIn('⚫ Black', content)
        self.assertIn('Rating 2850', content)
        self.assertIn('Rating 2820', content)
        self.assertIn('DRAW', content)
        self.assertIn('Game drawn', content)
        self.assertIn('Table #1', content)

        # Basketball stats MUST NOT be present
        self.assertNotIn('Field Goals', content)
        self.assertNotIn('Free Throws', content)
        self.assertNotIn('Rebounds', content)

    def test_chess_tournament_fixtures_card_shows_white_black_and_score(self):
        fx = Fixture.objects.create(
            tournament=self.chess_tourney,
            round_no=1,
            sequence=1,
            round_name='Round 1',
            status='COMPLETED',
        )
        p0 = FixtureParticipant.objects.create(
            fixture=fx, label='Magnus Carlsen', slot=0, score=Decimal('1.00'), is_winner=True, stats={'rating': 2850}
        )
        p1 = FixtureParticipant.objects.create(
            fixture=fx, label='Hikaru Nakamura', slot=1, score=Decimal('0.00'), is_winner=False, stats={'rating': 2820}
        )

        url = reverse('tournament_detail', kwargs={'slug': self.chess_tourney.slug})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        content = res.content.decode('utf-8')
        # Check white and black tags
        self.assertIn('⚪ White', content)
        self.assertIn('⚫ Black', content)
        self.assertIn('1', content)
        self.assertIn('0', content)

        self.assertNotIn('Full Team Rosters', content)

    def test_chess_match_detail_shows_winner_1_0(self):
        fx = Fixture.objects.create(
            tournament=self.chess_tourney,
            round_no=2,
            sequence=2,
            round_name='Round 2',
            status='COMPLETED',
        )
        p0 = FixtureParticipant.objects.create(
            fixture=fx, label='Viswanathan Anand', slot=0, score=Decimal('1.00'), is_winner=True
        )
        p1 = FixtureParticipant.objects.create(
            fixture=fx, label='Garry Kasparov', slot=1, score=Decimal('0.00'), is_winner=False
        )

        url = reverse('match_detail', kwargs={'slug': self.chess_tourney.slug, 'pk': fx.pk})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        content = res.content.decode('utf-8')
        self.assertIn('1', content)
        self.assertIn('0', content)
        self.assertIn('Viswanathan Anand', content)
        self.assertIn('Garry Kasparov', content)
        self.assertIn('won playing as', content)
        self.assertIn('White ⚪', content)

        # Test model property chess_score_display
        self.assertEqual(fx.chess_score_display, ('1', '0'))

    def test_racket_match_detail_shows_sets_breakdown(self):
        fx = Fixture.objects.create(
            tournament=self.pickleball_tourney,
            round_no=1,
            sequence=1,
            round_name='Quarterfinals',
            status='COMPLETED',
            set_scores=[{'a': 21, 'b': 18}, {'a': 19, 'b': 21}, {'a': 21, 'b': 15}],
            sets_to_win=2,
        )
        p0 = FixtureParticipant.objects.create(
            fixture=fx, label='Ben Johns', slot=0, score=Decimal('2.00'), is_winner=True
        )
        p1 = FixtureParticipant.objects.create(
            fixture=fx, label='Tyson McGuffin', slot=1, score=Decimal('1.00'), is_winner=False
        )

        url = reverse('match_detail', kwargs={'slug': self.pickleball_tourney.slug, 'pk': fx.pk})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        content = res.content.decode('utf-8')
        # Check sets won
        self.assertIn('FINAL (SETS)', content)
        self.assertIn('Set 1:', content)
        self.assertIn('Set 2:', content)
        self.assertIn('Set 3:', content)
        self.assertIn('21', content)
        self.assertIn('18', content)
        self.assertIn('Set-by-Set Score Breakdown', content)
        self.assertIn('Sets &amp; Points', content)

        # Basketball stats MUST NOT be present
        self.assertNotIn('Field Goals', content)
        self.assertNotIn('Free Throws', content)
        self.assertNotIn('Rebounds', content)

    def test_chess_tournament_fixtures_card_shows_white_black_and_score(self):
        fx = Fixture.objects.create(
            tournament=self.chess_tourney,
            round_no=1,
            sequence=1,
            round_name='Round 1',
            status='COMPLETED',
        )
        p0 = FixtureParticipant.objects.create(
            fixture=fx, label='Magnus Carlsen', slot=0, score=Decimal('1.00'), is_winner=True, stats={'rating': 2850}
        )
        p1 = FixtureParticipant.objects.create(
            fixture=fx, label='Hikaru Nakamura', slot=1, score=Decimal('0.00'), is_winner=False, stats={'rating': 2820}
        )

        url = reverse('tournament_detail', kwargs={'slug': self.chess_tourney.slug})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        content = res.content.decode('utf-8')
        # Check white and black tags
        self.assertIn('⚪ White', content)
        self.assertIn('⚫ Black', content)
        self.assertIn('1', content)
        self.assertIn('0', content)

