from django.db import migrations
from django.conf import settings

def backfill_achievements(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    try:
        from content.tasks import check_and_award_achievements_task
        for u in User.objects.all():
            try:
                check_and_award_achievements_task(u.id)
            except Exception:
                pass
    except ImportError:
        pass

class Migration(migrations.Migration):

    dependencies = [
        ('content', '0002_seed_achievements'),
    ]

    operations = [
        migrations.RunPython(backfill_achievements),
    ]
