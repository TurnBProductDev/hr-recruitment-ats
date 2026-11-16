"""Regressions behind the CV-Automation-Flow-Final failures of 2026-11-13/14.

Run against sqlite so the live Azure DB is never touched:
    DB_ENGINE=sqlite python manage.py test candidates.test_intake_regressions
"""
import base64
import json
import re
from unittest import mock

from django.conf import settings
from django.db import models
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Candidate


class IntakeProcedureColumnsTests(TestCase):
    """sp_intake_add_candidate (the careers-mailbox Logic App's insert)
    lists its columns explicitly, so a NOT NULL Candidate column it doesn't
    list - with only a Django-side default, which SQL Server never sees -
    fails every intake. Happened twice: suggested_role_text (2026-09-28) and
    cv_text_ref (2026-11-13). This fails at test time instead."""

    def test_every_required_column_is_in_the_procedures_insert(self):
        sql = (settings.BASE_DIR / 'sql' / 'sp_intake_add_candidate.sql').read_text(encoding='utf-8')
        listed = re.search(r'INSERT INTO dbo\.candidates_candidate\s*\((.*?)\)\s*VALUES', sql, re.S | re.I)
        self.assertIsNotNone(listed, 'Could not find the candidate INSERT in sp_intake_add_candidate.sql')
        listed = {c.strip().lower() for c in listed.group(1).split(',')}
        missing = [
            f.column for f in Candidate._meta.concrete_fields
            if not f.primary_key and not f.null and f.db_default is models.NOT_PROVIDED
            and f.column.lower() not in listed
        ]
        self.assertEqual(missing, [], 'Make these nullable (or give them a db_default), or add them to '
                                      'sql/sp_intake_add_candidate.sql AND redeploy the procedure.')

    def test_cv_text_ref_is_nullable(self):
        self.assertTrue(Candidate._meta.get_field('cv_text_ref').null)


@override_settings(CV_EXTRACT_API_KEY='k')
class LargeCvUploadTests(TestCase):
    def test_cv_over_the_old_2_5mb_limit_reaches_the_extractor(self):
        # A 7.4 MB PDF is ~10 MB once base64'd into the Logic App's JSON - over
        # Django's default DATA_UPLOAD_MAX_MEMORY_SIZE, so it was refused.
        body = json.dumps({'content_base64': base64.b64encode(b'%PDF-1.4 ' + b'x' * 7_400_000).decode(),
                           'filename': 'big.pdf'})
        with mock.patch('candidates.views.cv_extraction.extract_profile',
                        return_value={'full_name': 'Big CV'}) as extract:
            resp = self.client.post(reverse('cv_extract_api'), body, content_type='application/json',
                                    HTTP_X_API_KEY='k')
        self.assertEqual(resp.status_code, 200)
        extract.assert_called_once()
