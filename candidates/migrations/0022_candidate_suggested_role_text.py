from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('candidates', '0021_candidatestatushistory_is_undone'),
    ]

    operations = [
        migrations.AddField(
            model_name='candidate',
            name='suggested_role_text',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
