from django.db import migrations, models


def mark_existing(apps, schema_editor):
    """Flag every email that already has a Blacklist entry."""
    Blacklist = apps.get_model('candidates', 'Blacklist')
    EmailRegistry = apps.get_model('candidates', 'EmailRegistry')
    for bl in Blacklist.objects.select_related('candidate'):
        email = (bl.candidate.email or '').strip().lower()
        if email:
            EmailRegistry.objects.filter(email__iexact=email, blacklisted_at__isnull=True).update(
                blacklisted_at=bl.blacklisted_at)


class Migration(migrations.Migration):

    dependencies = [
        ('candidates', '0028_reject_reason'),
    ]

    operations = [
        migrations.AddField(
            model_name='emailregistry',
            name='blacklisted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing, migrations.RunPython.noop),
    ]
