from django.db import migrations, models

ROUND1, INTERVIEW, HOLD = 'ROUND1', 'INTERVIEW', 'SCREENING_HOLD'
COMPLETED = 'COMPLETED'


def _round_interviews(Interview, candidate_id, stage):
    qs = Interview.objects.filter(candidate_id=candidate_id)
    return qs.filter(round_type='ROUND1') if stage == ROUND1 else qs.exclude(round_type='ROUND1')


def backfill(apps, schema_editor):
    """Name every existing Round 1/Round 2 hold - the same rule
    services.hold_kind_for applies to new ones: RESULT if that round's
    interview had taken place (completed, and booked for before the hold was
    taken), else INTERVIEW. A history row whose remark is Mark Result's own
    "... interview put on hold." is a result hold by definition."""
    Candidate = apps.get_model('candidates', 'Candidate')
    History = apps.get_model('candidates', 'CandidateStatusHistory')
    Interview = apps.get_model('interviews', 'Interview')

    for row in History.objects.filter(new_status=HOLD, old_status__in=(ROUND1, INTERVIEW)):
        took_place = (_round_interviews(Interview, row.candidate_id, row.old_status)
                      .filter(status=COMPLETED, scheduled_date__lte=row.changed_at).exists())
        result = took_place or (row.remarks or '').endswith('interview put on hold.')
        row.hold_kind = 'RESULT' if result else 'INTERVIEW'
        row.save(update_fields=['hold_kind'])

    for c in Candidate.objects.filter(status=HOLD, hold_from_status__in=(ROUND1, INTERVIEW)):
        last = (History.objects.filter(candidate_id=c.pk, new_status=HOLD, is_undone=False)
                .order_by('-changed_at', '-id').first())
        if last is not None and last.hold_kind:
            c.hold_kind = last.hold_kind
        else:
            latest = _round_interviews(Interview, c.pk, c.hold_from_status).order_by('-pk').first()
            c.hold_kind = 'RESULT' if latest is not None and latest.status == COMPLETED else 'INTERVIEW'
        c.save(update_fields=['hold_kind'])


class Migration(migrations.Migration):

    dependencies = [
        ('candidates', '0023_intake_message_id_and_linked_attachments'),
        ('interviews', '0010_alter_interview_round_type_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='candidate',
            name='hold_kind',
            field=models.CharField(blank=True, choices=[('INTERVIEW', 'Interview Hold'), ('RESULT', 'Interview Result Hold')], max_length=20, null=True),
        ),
        migrations.AddField(
            model_name='candidatestatushistory',
            name='hold_kind',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
