# Generated manually to add pool-membership denormalisation for individual
# (registration-based) sports, mirroring TournamentTeamEntry.group_name —
# needed to extend Pool Stage + Knockout to badminton/pickleball.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tournaments', '0010_teammembership_phone_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='individualregistration',
            name='group_name',
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
