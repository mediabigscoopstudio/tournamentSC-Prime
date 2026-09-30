from django.db import migrations

def grandfather_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.all().update(onboarding_complete=True)

class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0009_rename_city_playerprofile_current_city_and_more'),
    ]

    operations = [
        migrations.RunPython(grandfather_users, reverse_code=migrations.RunPython.noop),
    ]
