# Generated manually to add Swiss-format setup storage, mirroring
# Tournament.pool_config's shape/spirit.

from django.db import migrations, models

import tournaments.constants


class Migration(migrations.Migration):

    dependencies = [
        ('tournaments', '0011_individualregistration_group_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='tournament',
            name='swiss_config',
            field=models.JSONField(blank=True, default=tournaments.constants.default_swiss_config),
        ),
    ]
