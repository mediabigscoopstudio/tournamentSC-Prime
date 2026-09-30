from django.db import migrations

def fix_empty_strings(apps, schema_editor):
    PlayerProfile = apps.get_model('accounts', 'PlayerProfile')
    PlayerProfile.objects.filter(school="").update(school="[]")
    PlayerProfile.objects.filter(college="").update(college="[]")
    PlayerProfile.objects.filter(workplace="").update(workplace="[]")

class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_grandfather_existing_users'),
    ]

    operations = [
        migrations.RunPython(fix_empty_strings, reverse_code=migrations.RunPython.noop),
    ]
