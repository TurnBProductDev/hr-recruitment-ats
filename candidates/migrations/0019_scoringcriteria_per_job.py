import django.db.models.deletion
from django.db import migrations, models


def clear_existing_criteria(apps, schema_editor):
    """ScoringCriteria was a single global row (pk=1) until now - nothing
    meaningful to carry forward once it becomes one row per role (the
    feature had just shipped with nobody's saved text in it yet)."""
    ScoringCriteria = apps.get_model('candidates', 'ScoringCriteria')
    ScoringCriteria.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('candidates', '0018_scoringcriteria'),
        ('jobs', '0005_job_job_type_job_must_have_requirements'),
    ]

    operations = [
        migrations.RunPython(clear_existing_criteria, migrations.RunPython.noop),
        migrations.AddField(
            model_name='scoringcriteria',
            name='job',
            field=models.OneToOneField(
                default=1,  # unused - the table is empty by the time this runs (see above)
                on_delete=django.db.models.deletion.CASCADE,
                related_name='scoring_criteria', to='jobs.job',
            ),
            preserve_default=False,
        ),
    ]
