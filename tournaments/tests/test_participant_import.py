import datetime
import io

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import OrganizerProfile
from tournaments.models import Sport, Team, TeamMembership, Tournament, TournamentTeamEntry
from tournaments.participant_import import parse_participants

User = get_user_model()


def _make_tournament(sport_slug='basketball', organizer=None):
    sport, _ = Sport.objects.get_or_create(
        slug=sport_slug, defaults={'name': sport_slug.title(), 'format_type': 'TEAM'})
    return Tournament.objects.create(
        name=f'{sport.name} Cup', sport=sport, organizer=organizer,
        format='KNOCKOUT', start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 5))


class ParseParticipantsTests(TestCase):
    HEADER = 'Team Name,Participant Name,Jersey Number,Phone Number\n'

    def test_multiple_participants_same_team_one_paste(self):
        text = self.HEADER + 'Lakers,Amit,7,\nLakers,Rohan,8,\n'
        parsed = parse_participants(pasted_text=text)
        self.assertEqual(len(parsed.rows), 2)
        self.assertFalse(any(r.error for r in parsed.rows))
        self.assertEqual({r.team_name for r in parsed.rows}, {'Lakers'})

    def test_blank_rows_skipped(self):
        text = self.HEADER + 'Lakers,Amit,7,\n\nLakers,Rohan,8,\n'
        parsed = parse_participants(pasted_text=text)
        self.assertEqual(parsed.skipped_blank, 1)
        self.assertEqual(len(parsed.rows), 2)

    def test_missing_jersey_is_row_error_only(self):
        text = self.HEADER + 'Lakers,Amit,,\nLakers,Rohan,8,\n'
        parsed = parse_participants(pasted_text=text)
        self.assertEqual(len(parsed.rows), 2)
        self.assertIn('jersey number is required', parsed.rows[0].error)
        self.assertEqual(parsed.rows[1].error, '')

    def test_blank_phone_is_valid(self):
        text = self.HEADER + 'Lakers,Amit,7,\n'
        parsed = parse_participants(pasted_text=text)
        self.assertEqual(parsed.rows[0].error, '')
        self.assertEqual(parsed.rows[0].phone, '')

    def test_malformed_phone_errors_only_that_row(self):
        text = self.HEADER + 'Lakers,Amit,7,abc\nLakers,Rohan,8,9876543210\n'
        parsed = parse_participants(pasted_text=text)
        self.assertIn('not valid', parsed.rows[0].error)
        self.assertEqual(parsed.rows[1].error, '')

    def test_missing_required_columns_aborts_with_file_level_error(self):
        text = 'Team Name,Jersey Number\nLakers,7\n'
        parsed = parse_participants(pasted_text=text)
        self.assertTrue(parsed.errors)
        self.assertEqual(parsed.rows, [])

    def test_paste_text_tab_delimited_parses(self):
        text = 'Team Name\tParticipant Name\tJersey Number\tPhone Number\nLakers\tAmit\t7\t\n'
        parsed = parse_participants(pasted_text=text)
        self.assertEqual(len(parsed.rows), 1)
        self.assertEqual(parsed.rows[0].participant_name, 'Amit')

    def test_paste_text_comma_delimited_parses(self):
        text = self.HEADER + 'Lakers,Amit,7,\n'
        parsed = parse_participants(pasted_text=text)
        self.assertEqual(len(parsed.rows), 1)
        self.assertEqual(parsed.rows[0].team_name, 'Lakers')

    def test_file_upload_csv_parses(self):
        csv_bytes = (self.HEADER + 'Lakers,Amit,7,\n').encode('utf-8')
        upload = io.BytesIO(csv_bytes)
        upload.name = 'roster.csv'
        parsed = parse_participants(uploaded_file=upload)
        self.assertEqual(len(parsed.rows), 1)
        self.assertEqual(parsed.rows[0].participant_name, 'Amit')


class ParticipantsBulkImportViewTests(TestCase):
    HEADER = 'Team Name,Participant Name,Jersey Number,Phone Number\n'

    def setUp(self):
        self.user = User.objects.create_user(username='org1', email='org1@example.com', password='pw12345')
        self.organizer = OrganizerProfile.objects.create(user=self.user, is_approved=True)
        self.tournament = _make_tournament('basketball', organizer=self.organizer)
        self.client.force_login(self.user)

    def _import(self, text):
        return self.client.post(
            reverse('participants_bulk_import', args=[self.tournament.slug]),
            {'participants_text': text})

    def test_new_team_new_participant_creates_both(self):
        self._import(self.HEADER + 'Lakers,Amit,7,9876543210\n')
        team = Team.objects.get(name='Lakers', sport=self.tournament.sport)
        self.assertTrue(TournamentTeamEntry.objects.filter(tournament=self.tournament, team=team).exists())
        membership = TeamMembership.objects.get(team=team, display_name='Amit')
        self.assertEqual(membership.jersey_number, '7')
        self.assertEqual(membership.phone_number, '9876543210')

    def test_existing_team_new_participant_reuses_team(self):
        team = Team.objects.create(name='Lakers', sport=self.tournament.sport)
        TournamentTeamEntry.objects.create(tournament=self.tournament, team=team, status='APPROVED')
        self._import(self.HEADER + 'Lakers,Amit,7,\n')
        self.assertEqual(Team.objects.filter(name__iexact='Lakers', sport=self.tournament.sport).count(), 1)
        self.assertTrue(TeamMembership.objects.filter(team=team, display_name='Amit').exists())

    def test_reimport_is_idempotent(self):
        text = self.HEADER + 'Lakers,Amit,7,\nLakers,Rohan,8,\n'
        self._import(text)
        self._import(text)
        self.assertEqual(Team.objects.filter(name__iexact='Lakers', sport=self.tournament.sport).count(), 1)
        self.assertEqual(TeamMembership.objects.filter(team__name__iexact='Lakers').count(), 2)

    def test_non_basketball_sport_rejected(self):
        other = _make_tournament('mobile-esports', organizer=self.organizer)
        resp = self.client.post(
            reverse('participants_bulk_import', args=[other.slug]),
            {'participants_text': self.HEADER + 'Team A,Amit,7,\n'}, follow=True)
        self.assertFalse(Team.objects.filter(name='Team A').exists())
        self.assertContains(resp, 'only available for basketball')

    def test_requires_organizer_ownership(self):
        other_user = User.objects.create_user(username='org2', email='org2@example.com', password='pw12345')
        OrganizerProfile.objects.create(user=other_user, is_approved=True)
        self.client.force_login(other_user)
        resp = self.client.post(
            reverse('participants_bulk_import', args=[self.tournament.slug]),
            {'participants_text': self.HEADER + 'Lakers,Amit,7,\n'})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Team.objects.filter(name='Lakers').exists())

    def test_file_upload_path_works(self):
        csv_bytes = (self.HEADER + 'Warriors,Steph,30,\n').encode('utf-8')
        upload = io.BytesIO(csv_bytes)
        upload.name = 'roster.csv'
        self.client.post(
            reverse('participants_bulk_import', args=[self.tournament.slug]),
            {'participants_file': upload})
        self.assertTrue(Team.objects.filter(name='Warriors', sport=self.tournament.sport).exists())

    def test_case_insensitive_whitespace_team_match(self):
        team = Team.objects.create(name='Lakers', sport=self.tournament.sport)
        TournamentTeamEntry.objects.create(tournament=self.tournament, team=team, status='APPROVED')
        self._import(self.HEADER + '  lakers  ,Amit,7,\n')
        self.assertEqual(Team.objects.filter(sport=self.tournament.sport).count(), 1)

    def test_basketball_page_shows_combined_import_box(self):
        resp = self.client.get(reverse('participants_manage', args=[self.tournament.slug]))
        self.assertContains(resp, 'Bulk Import Roster')
        self.assertContains(resp, 'name="participants_file"')
        self.assertNotContains(resp, 'Import Team Roster')

    def test_non_basketball_page_keeps_original_team_only_box(self):
        other = _make_tournament('mobile-esports', organizer=self.organizer)
        resp = self.client.get(reverse('participants_manage', args=[other.slug]))
        self.assertContains(resp, 'Bulk Team Import')
        self.assertContains(resp, 'name="teams_file"')
        self.assertNotContains(resp, 'participants_text')
