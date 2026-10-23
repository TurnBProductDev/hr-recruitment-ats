from django.db import migrations

# Interview slots are 30 minutes (interviews.models.INTERVIEW_DURATION), but the
# two "propose slots" emails still asked for "one-hour slots". Only rows that
# still contain that literal phrase are touched, so wording an admin has
# already rewritten is left alone.
KEYS = ('interviewer_new_request', 'interviewer_new_slots_needed')
OLD, NEW = 'one-hour slots', 'thirty-minute slots'


def _swap(apps, old, new):
    EmailTemplate = apps.get_model('email_templates', 'EmailTemplate')
    for row in EmailTemplate.objects.filter(key__in=KEYS, body__contains=old):
        row.body = row.body.replace(old, new)
        row.save(update_fields=['body'])


def forwards(apps, schema_editor):
    _swap(apps, OLD, NEW)


def backwards(apps, schema_editor):
    _swap(apps, NEW, OLD)


class Migration(migrations.Migration):
    dependencies = [
        ('email_templates', '0006_seed_interview_result_email'),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
