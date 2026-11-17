"""Word (.docx) CVs open as a web page instead of silently downloading.

    DB_ENGINE=sqlite python manage.py test candidates.test_cv_preview
"""
import io
import shutil
import tempfile
import zipfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from . import cv_preview
from .models import Candidate
from .permissions import RECRUITER

BLOB = 'https://acct.blob.core.windows.net/candidate-cvs/2026-abc-Anoob_Resume.docx'


def _docx(paragraphs):
    """A minimal valid .docx with the given paragraph texts."""
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    body = ''.join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml',
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/'
                   'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels',
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml',
                   f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


class IsDocxTests(TestCase):
    def test_detects_docx_names_and_urls(self):
        self.assertTrue(cv_preview.is_docx(BLOB))
        self.assertTrue(cv_preview.is_docx('cvs/1/ID1/resume.DOCX'))
        self.assertTrue(cv_preview.is_docx('https://x.blob.core.windows.net/c/My%20CV.docx'))
        self.assertFalse(cv_preview.is_docx('cvs/1/ID1/resume.pdf'))
        self.assertFalse(cv_preview.is_docx('cvs/1/ID1/resume.doc'))
        self.assertFalse(cv_preview.is_docx(''))


@override_settings(AZURE_STORAGE_ACCOUNT_NAME='acct', AZURE_STORAGE_ACCOUNT_KEY='a2V5')
class WordCvViewTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        user = get_user_model().objects.create_user('rec', 'rec@example.com', 'pw')
        user.groups.add(Group.objects.get_or_create(name=RECRUITER)[0])
        self.client.force_login(user)
        self.candidate = Candidate.objects.create(full_name='Mohammed Anoob TJ', email='a@example.com')
        self.url = reverse('candidate_cv', args=[self.candidate.pk])

    def _mock_blob(self, content):
        resp = mock.Mock(status_code=200, content=content, headers={})
        resp.raise_for_status = mock.Mock()
        return mock.patch('candidates.views.requests.get', return_value=resp)

    def test_blob_docx_is_shown_as_a_web_page(self):
        self.candidate.resume_url = BLOB
        self.candidate.save()
        with self._mock_blob(_docx(['Sales Associate at Lulu', '<script>alert(1)</script>'])):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/html'))
        self.assertIn("default-src 'none'", response['Content-Security-Policy'])
        body = response.content.decode()
        self.assertIn('Sales Associate at Lulu', body)
        self.assertNotIn('<script>alert(1)</script>', body)  # document text is escaped
        self.assertIn(self.url + '?download=1', body)

    def test_download_original_still_redirects_to_the_file(self):
        self.candidate.resume_url = BLOB
        self.candidate.save()
        response = self.client.get(self.url + '?download=1')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(BLOB + '?'))

    def test_uploaded_docx_is_shown_as_a_web_page(self):
        self.candidate.resume_blob_url.save('cv.docx', ContentFile(_docx(['Deputy Manager at HDFC'])))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Deputy Manager at HDFC')

    def test_unreadable_docx_falls_back_to_download(self):
        self.candidate.resume_url = BLOB
        self.candidate.save()
        with self._mock_blob(b'not really a docx'):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_pdf_cv_is_unchanged(self):
        self.candidate.resume_url = BLOB.replace('.docx', '.pdf')
        self.candidate.save()
        with mock.patch('candidates.views.requests.get') as get:
            response = self.client.get(self.url)
        get.assert_not_called()
        self.assertEqual(response.status_code, 302)
