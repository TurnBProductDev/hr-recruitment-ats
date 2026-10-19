"""
Find - and, once reviewed, remove - careers-mailbox intake rows that should
never have been candidates:

  * a non-CV attachment (brochure, certificate, placement report, ...)
    filed as an applicant of its own, and
  * the second row an email produced when it carried a CV *and* a cover
    letter (or the CV twice) - the "same candidate shown as reapply" case.

Both were made impossible for new emails by CV-Automation-Flow-Final's
document_type check + sp_intake_add_candidate's @source_message_id; this
cleans up what came in before that.

Two steps, so nothing is deleted without a human looking at the list first:

    python manage.py cleanup_intake_rows review --plan intake_cleanup.json
        Read-only. Re-reads each suspect row's file with the CV reader to tell a
        CV from a cover letter/other document, prints the proposed action per
        row and saves the plan (JSON) for step 2. Costs one Azure OpenAI call
        per suspect row.

    python manage.py cleanup_intake_rows apply --plan intake_cleanup.json --ids 8092,8093
        Carries out the plan for exactly the listed candidate ids (all of them
        with --ids all): a cover letter/other document from a split email is
        first attached to the candidate its CV created, then the row is
        deleted; the email registry's count/first-candidate and that kept
        candidate's reapply flag are corrected to match.

    python manage.py cleanup_intake_rows apply --plan intake_cleanup.json --attach 8051:7533
        For a row the review left as manual_check (or any other row in the
        plan): attach its file to the candidate you name, then delete it - e.g.
        documents a candidate emailed in later, filed as a candidate of their
        own. Combine with --ids in the same run if you like.

Suspects (only careers-intake rows are ever considered):
    split    - 2+ candidates with the same email from the same email message
               (same applied timestamp - the procedure stamps every row from
               one email with that email's received time).
    junk     - a placeholder email, a name that is just a file name, or no
               education and no experience rows at all.
"""
import json
import re
from collections import defaultdict

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from candidates import cv_extraction, cv_storage
from candidates.models import PLACEHOLDER_EMAIL_DOMAIN, Attachment, Candidate, EmailRegistry

INTAKE_PERFORMED_BY = 'Careers Intake'  # sp_intake_add_candidate's history row
FILENAME_NAME = re.compile(r'\.(pdf|docx?)$', re.IGNORECASE)
KEEP, DELETE, ATTACH_DELETE, CHECK = 'keep', 'delete', 'attach_then_delete', 'manual_check'


def _intake_candidates():
    return Candidate.objects.filter(history__performed_by=INTAKE_PERFORMED_BY).distinct()


def _file_bytes(candidate):
    """The row's own file, or None if it can't be fetched from here (a
    SharePoint sharing link needs a signed-in browser)."""
    url = (candidate.resume_url or '').strip()
    if url and cv_storage.is_our_blob_url(url):
        response = requests.get(cv_storage.sas_url(url), timeout=(10, 60))
        response.raise_for_status()
        return response.content
    if candidate.resume_blob_url:
        with candidate.resume_blob_url.open('rb') as fh:
            return fh.read()
    return None


def _file_name(candidate):
    url = candidate.resume_url or (candidate.resume_blob_url.name if candidate.resume_blob_url else '')
    return requests.utils.unquote(url.rsplit('/', 1)[-1]) if url else ''


def _classify(candidate):
    """'cv' / 'cover_letter' / 'other', or 'unknown' when the file can't be
    read - never guessed."""
    try:
        content = _file_bytes(candidate)
        if not content:
            return 'unknown', 'file not reachable from here (SharePoint link?)'
        fields = cv_extraction.extract_profile(content, filename=_file_name(candidate))
        return fields['document_type'], ''
    except (requests.RequestException, cv_extraction.CVExtractionError, OSError) as exc:
        return 'unknown', str(exc)[:120]


def _acted_on(candidate):
    """Anything HR did to this row beyond it arriving - surfaced in the review
    so a row someone already worked on isn't deleted unnoticed."""
    bits = []
    if candidate.status != Candidate.Status.OPEN:
        bits.append(candidate.get_status_display())
    if candidate.interviews.exists():
        bits.append('interviews')
    if candidate.communication_logs.exists():
        bits.append('call log')
    if candidate.notes.exists():
        bits.append('notes')
    return ', '.join(bits)


class Command(BaseCommand):
    help = 'Review, then delete, careers-intake rows that are not real applications (see module docstring).'

    def add_arguments(self, parser):
        parser.add_argument('step', choices=['review', 'apply'])
        parser.add_argument('--plan', required=True, help='Plan JSON file - written by review, read by apply.')
        parser.add_argument('--ids', help="apply only: comma-separated candidate ids from the plan, or 'all'.")
        parser.add_argument('--attach', help='apply only: comma-separated ROW:CANDIDATE pairs - attach the '
                                             "row's file to CANDIDATE, then delete the row.")

    def handle(self, *args, **opts):
        if opts['step'] == 'review':
            self._review(opts['plan'])
        else:
            if not (opts['ids'] or opts['attach']):
                raise CommandError('apply needs --ids (ids from the plan, or "all") and/or --attach ROW:CANDIDATE.')
            self._apply(opts['plan'], opts['ids'] or '', opts['attach'] or '')

    # ------------------------------------------------------------------ review
    def _review(self, plan_path):
        intake = _intake_candidates()

        split_keys = (intake.exclude(email__endswith=f'@{PLACEHOLDER_EMAIL_DOMAIN}')
                      .values('email', 'created_at').annotate(n=Count('id', distinct=True)).filter(n__gt=1))
        groups = [list(intake.filter(email=k['email'], created_at=k['created_at']).order_by('id'))
                  for k in split_keys]
        in_group = {c.pk for g in groups for c in g}

        junk = [c for c in intake.exclude(pk__in=in_group).prefetch_related('education', 'experience_set')
                if (c.email or '').endswith(f'@{PLACEHOLDER_EMAIL_DOMAIN}')
                or FILENAME_NAME.search(c.full_name or '')
                or (not c.education.all() and not c.experience_set.all())]

        self.stdout.write(f'{len(groups)} split email(s) ({len(in_group)} rows), '
                          f'{len(junk)} other suspect row(s) - reading each file...')
        rows = []

        for group in groups:
            # The very same file filed twice (identical link) needs no reading -
            # the later row is just a copy of the first.
            seen, unique = {}, []
            for c in group:
                url = (c.resume_url or '').strip()
                if url and url in seen:
                    rows.append(self._row(c, 'split', 'same file', DELETE, seen[url], ''))
                else:
                    seen.setdefault(url, c.pk)
                    unique.append(c)
            if len(unique) == 1:
                rows.append(self._row(unique[0], 'split', 'cv', KEEP, None, ''))
                continue
            typed = [(c, *_classify(c)) for c in unique]
            cvs = [c for c, t, _ in typed if t == 'cv']
            # Keep the CV row HR has taken furthest (else the first one); with
            # no readable CV in the group, keep everything for a manual look.
            keep = max(cvs, key=lambda c: (c.status != Candidate.Status.OPEN, -c.pk)) if cvs else None
            for c, doc_type, note in typed:
                if keep is None or doc_type == 'unknown':
                    action, target = CHECK, None
                elif c.pk == keep.pk:
                    action, target = KEEP, None
                elif doc_type == 'cv':
                    action, target = DELETE, keep.pk  # the same CV a second time
                else:
                    action, target = ATTACH_DELETE, keep.pk
                rows.append(self._row(c, 'split', doc_type, action, target, note))

        for c in junk:
            doc_type, note = _classify(c)
            action = {'cv': KEEP, 'unknown': CHECK}.get(doc_type, DELETE)
            rows.append(self._row(c, 'junk', doc_type, action, None, note))

        with open(plan_path, 'w', encoding='utf-8') as fh:
            json.dump(rows, fh, indent=2, default=str)

        self.stdout.write('')
        for r in rows:
            target = f' -> #{r["attach_to"]}' if r['attach_to'] else ''
            acted = f'  [HR ACTED: {r["acted_on"]}]' if r['acted_on'] else ''
            self.stdout.write(
                f'{r["action"]:<19} #{r["id"]:<6} {r["kind"]:<5} {r["document_type"]:<12} '
                f'{r["name"][:38]:<38} {r["email"][:34]:<34} {r["job"][:28]}{target}{acted}'
                + (f'  ({r["note"]})' if r['note'] else ''))
        to_remove = [r for r in rows if r['action'] in (DELETE, ATTACH_DELETE)]
        self.stdout.write(self.style.SUCCESS(
            f'\n{len(to_remove)} row(s) proposed for removal. Plan saved to {plan_path} - nothing changed.'))

    def _row(self, c, kind, doc_type, action, attach_to, note):
        return {
            'id': c.pk, 'code': c.candidate_code, 'name': c.full_name or '', 'email': c.email or '',
            'job': c.job.title if c.job else '', 'status': c.status, 'applied': c.created_at,
            'kind': kind, 'document_type': doc_type, 'action': action, 'attach_to': attach_to,
            'acted_on': _acted_on(c), 'note': note,
            'file_url': c.resume_url or '', 'file_name': _file_name(c),
        }

    # ------------------------------------------------------------------- apply
    def _apply(self, plan_path, ids_arg, attach_arg):
        with open(plan_path, encoding='utf-8') as fh:
            everything = {r['id']: r for r in json.load(fh)}
        plan = {pk: r for pk, r in everything.items() if r['action'] in (DELETE, ATTACH_DELETE)}
        if ids_arg.strip().lower() == 'all':
            ids = list(plan)
        else:
            ids = [int(i) for i in ids_arg.split(',') if i.strip()]
            unknown = [i for i in ids if i not in plan]
            if unknown:
                raise CommandError(f'Not proposed for removal in {plan_path}: {unknown}')
        for pair in (p for p in attach_arg.split(',') if p.strip()):
            row_id, _, target_id = pair.partition(':')
            row_id, target_id = int(row_id), int(target_id)
            if row_id not in everything:
                raise CommandError(f'#{row_id} is not in {plan_path}.')
            if not Candidate.objects.filter(pk=target_id).exists():
                raise CommandError(f'--attach target #{target_id} does not exist.')
            plan[row_id] = {**everything[row_id], 'action': ATTACH_DELETE, 'attach_to': target_id}
            if row_id not in ids:
                ids.append(row_id)

        emails = defaultdict(int)
        with transaction.atomic():
            for pk in ids:
                r = plan[pk]
                candidate = Candidate.objects.filter(pk=pk).first()
                if candidate is None:
                    self.stdout.write(f'#{pk} already gone - skipped')
                    continue
                if r['action'] == ATTACH_DELETE:
                    keep = Candidate.objects.get(pk=r['attach_to'])
                    label = cv_extraction.DOCUMENT_TYPE_LABELS.get(r['document_type'], 'Other document')
                    attachment = Attachment(candidate=keep, label=f'{label} - {r["file_name"] or "attachment"}'[:255],
                                            url=candidate.resume_url or None)
                    if not attachment.url and candidate.resume_blob_url:
                        attachment.file = candidate.resume_blob_url.name
                    attachment.save()
                emails[(candidate.email or '').lower()] += 1
                candidate.delete()
                self.stdout.write(f'#{pk} {r["name"][:40]} - deleted'
                                  + (f' (attached to #{r["attach_to"]})' if r['action'] == ATTACH_DELETE else ''))
            for email in emails:
                self._fix_registry(email)
        self.stdout.write(self.style.SUCCESS(f'Removed {sum(emails.values())} row(s).'))

    def _fix_registry(self, email):
        """Recount the registry from the candidates that are left, and un-flag
        the earliest one as a reapply if it was only "duplicate" of a row
        that's now gone."""
        entry = EmailRegistry.objects.filter(email__iexact=email).first()
        remaining = list(Candidate.objects.filter(email__iexact=email).order_by('created_at', 'pk'))
        if entry is None:
            return
        if not remaining:
            entry.delete()
            return
        entry.application_count = len(remaining)
        entry.first_candidate = remaining[0]
        entry.save(update_fields=['application_count', 'first_candidate'])
        if remaining[0].is_duplicate:
            Candidate.objects.filter(pk=remaining[0].pk).update(is_duplicate=False)
