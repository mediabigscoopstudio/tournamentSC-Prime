from django.db import migrations

def seed_achievements(apps, schema_editor):
    Achievement = apps.get_model('content', 'Achievement')
    achievements = [
        {'name': 'First Tournament', 'icon': '🎯', 'description': 'Played your first tournament', 'criteria': {'tournaments': 1}},
        {'name': 'Veteran', 'icon': '⚔️', 'description': 'Played 5 tournaments', 'criteria': {'tournaments': 5}},
        {'name': 'First Win', 'icon': '🥇', 'description': 'Secured your first win', 'criteria': {'wins': 1}},
        {'name': 'Winner', 'icon': '🏆', 'description': 'Secured 10 wins', 'criteria': {'wins': 10}},
        {'name': 'Champion', 'icon': '👑', 'description': 'Secured 50 wins', 'criteria': {'wins': 50}},
    ]
    for a in achievements:
        Achievement.objects.get_or_create(
            name=a['name'],
            defaults={'icon': a['icon'], 'description': a['description'], 'criteria': a['criteria']}
        )

class Migration(migrations.Migration):

    dependencies = [
        ('content', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_achievements),
    ]
