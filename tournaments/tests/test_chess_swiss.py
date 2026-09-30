from decimal import Decimal
from django.test import TestCase

from accounts.models import User, PlayerProfile
from tournaments.models import Tournament, Sport, Fixture, FixtureParticipant, IndividualRegistration
from tournaments.engines import get_engine

class ChessSwissTests(TestCase):
    def setUp(self):
        self.sport = Sport.objects.create(slug='chess', name='Chess', format_type='INDIVIDUAL', default_format='SWISS')
        self.org_user = User.objects.create_user(email='org@example.com', password='pwd')
        from accounts.models import OrganizerProfile; self.org = OrganizerProfile.objects.create(user=self.org_user)
        self.org.is_approved = True
        self.org.save()
        
        self.tournament = Tournament.objects.create(
            organizer=self.org,
            sport=self.sport,
            format='SWISS',
            slug='test-chess-swiss',
            name='Test Chess Swiss', start_date='2023-01-01', end_date='2023-01-05'
        )
        self.tournament.swiss_config = {'num_rounds': 5}
        self.tournament.save()
        self.engine = get_engine(self.tournament)

    def _add_players(self, count):
        for i in range(1, count + 1):
            IndividualRegistration.objects.create(
                tournament=self.tournament,
                display_name=f'Player {i}',
                status='APPROVED',
                rating=1000 + i * 10
            )

    def test_8_player_multi_round(self):
        self._add_players(8)
        
        # Round 1
        count = self.engine.generate_fixtures()
        self.assertEqual(count, 4)
        
        # Record R1 results
        fixtures = list(self.tournament.fixtures.filter(round_no=1).order_by('sequence'))
        # Give W, W, D, W
        self.engine.record_result(fixtures[0], {'finalize': True, str(fixtures[0].participants.all()[0].id): {'score': 1}, str(fixtures[0].participants.all()[1].id): {'score': 0}})
        self.engine.record_result(fixtures[1], {'finalize': True, str(fixtures[1].participants.all()[0].id): {'score': 0}, str(fixtures[1].participants.all()[1].id): {'score': 1}})
        self.engine.record_result(fixtures[2], {'finalize': True, str(fixtures[2].participants.all()[0].id): {'score': 0.5}, str(fixtures[2].participants.all()[1].id): {'score': 0.5}})
        self.engine.record_result(fixtures[3], {'finalize': True, str(fixtures[3].participants.all()[0].id): {'score': 1}, str(fixtures[3].participants.all()[1].id): {'score': 0}})

        # Ensure Standings are correct
        standings = list(self.tournament.standings.order_by('position'))
        self.assertEqual(len(standings), 8)
        
        # Round 2
        count = self.engine.generate_next_round()
        self.assertEqual(count, 4)
        fixtures = list(self.tournament.fixtures.filter(round_no=2).order_by('sequence'))
        
        for fx in fixtures:
            self.engine.record_result(fx, {'finalize': True, str(fx.participants.all()[0].id): {'score': 0.5}, str(fx.participants.all()[1].id): {'score': 0.5}})
            
        # Round 3
        self.engine.generate_next_round()
        fixtures = list(self.tournament.fixtures.filter(round_no=3).order_by('sequence'))
        for fx in fixtures:
            self.engine.record_result(fx, {'finalize': True, str(fx.participants.all()[0].id): {'score': 1}, str(fx.participants.all()[1].id): {'score': 0}})
            
        # Round 4
        self.engine.generate_next_round()
        fixtures = list(self.tournament.fixtures.filter(round_no=4).order_by('sequence'))
        for fx in fixtures:
            # check colors
            parts = fx.participants.all().order_by('slot')
            self.assertEqual(parts[0].slot, 0) # White
            self.assertEqual(parts[1].slot, 1) # Black
            self.engine.record_result(fx, {'finalize': True, str(fx.participants.all()[0].id): {'score': 0}, str(fx.participants.all()[1].id): {'score': 1}})
            
        # Verify no repeats in round 4
        played = set()
        for fx in self.tournament.fixtures.filter(status='COMPLETED'):
            parts = list(fx.participants.all())
            if len(parts) == 2:
                k = tuple(sorted([parts[0].name, parts[1].name]))
                self.assertNotIn(k, played)
                played.add(k)

    def test_odd_players_bye_logic(self):
        self._add_players(5)
        self.engine.generate_fixtures()
        fixtures = list(self.tournament.fixtures.filter(round_no=1).order_by('sequence'))
        self.assertEqual(len(fixtures), 3) # 2 pairs, 1 bye
        
        # Bye fixture
        bye_fx = [fx for fx in fixtures if fx.participants.count() == 1][0]
        bye_player = bye_fx.participants.first()
        self.assertEqual(bye_player.score, 1) # 1 point
        
        # Record others
        for fx in fixtures:
            if fx != bye_fx:
                self.engine.record_result(fx, {'finalize': True, str(fx.participants.all()[0].id): {'score': 1}, str(fx.participants.all()[1].id): {'score': 0}})
        
        standings = {s.name: s for s in self.tournament.standings.all()}
        # Bye player gets 1 point
        self.assertEqual(standings[bye_player.name].points, 1.0)
        self.assertEqual(standings[bye_player.name].extra_stats['byes'], 1)

    def test_20_player_5_rounds(self):
        self._add_players(20)
        
        for r in range(1, 6):
            if r == 1:
                self.engine.generate_fixtures()
            else:
                self.engine.generate_next_round()
                
            fixtures = list(self.tournament.fixtures.filter(round_no=r).order_by('sequence'))
            self.assertEqual(len(fixtures), 10)
            for fx in fixtures:
                p0, p1 = fx.participants.all().order_by('slot')
                self.engine.record_result(fx, {'finalize': True, str(p0.id): {'score': 1}, str(p1.id): {'score': 0}})
                
        # Check no repeats across all 5 rounds
        played = set()
        for fx in self.tournament.fixtures.filter(status='COMPLETED'):
            parts = list(fx.participants.all())
            k = tuple(sorted([parts[0].name, parts[1].name]))
            self.assertNotIn(k, played, f"Repeated matchup found! {k}")
            played.add(k)
