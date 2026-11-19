"""Sort rejections made before Reject Reasons existed into the new reasons.

Two steps, so HR reviews everything before it's saved:

  1. python manage.py suggest_reject_reasons --export review.xlsx
     Read-only. Lists every current rejection with no reason recorded - its
     stage, remarks and a suggested reason (candidates/reject_reason_suggest.py).
     HR can change the "Reason" column (pick any label from the list sheet).

  2. python manage.py suggest_reject_reasons --apply review.xlsx [--dry-run]
     Saves the reviewed reason onto each rejection. Only fills rejections that
     still have no reason; the original remarks are never changed.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from candidates import reject_reasons as R
from candidates.models import Candidate, CandidateStatusHistory
from candidates.reject_reason_suggest import suggest
from candidates.views import _with_rejection_info, rejection_stage

HEADERS = ['History ID', 'Candidate Code', 'Candidate', 'Vacancy', 'Rejected On', 'Stage', 'Remarks',
           'Suggested Reason', 'Reason']


def _rows():
    qs = (_with_rejection_info(Candidate.objects.filter(status=Candidate.Status.REJECTED))
          .select_related('job').order_by('-rej_at'))
    latest = {}
    for c in qs:
        if c.rej_reason:
            continue
        row = (CandidateStatusHistory.objects
               .filter(candidate=c, new_status=Candidate.Status.REJECTED, is_undone=False)
               .order_by('-changed_at', '-id').first())
        if row is None or row.reject_reason:
            continue
        latest[row.pk] = (c, row)
    return latest.values()


class Command(BaseCommand):
    help = 'Suggest (export) or save (apply) Reject Reasons for rejections recorded before reasons existed.'

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--export', metavar='XLSX')
        group.add_argument('--apply', metavar='XLSX')
        parser.add_argument('--dry-run', action='store_true', help='With --apply: show what would change only.')

    def handle(self, *args, **opts):
        if opts['export']:
            return self._export(opts['export'])
        return self._apply(opts['apply'], opts['dry_run'])

    def _export(self, path):
        import openpyxl
        from openpyxl.styles import Font
        from openpyxl.worksheet.datavalidation import DataValidation

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Review'
        ws.append(HEADERS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        counts = {}
        for c, row in _rows():
            code = suggest(row.remarks)
            counts[code] = counts.get(code, 0) + 1
            ws.append([row.pk, c.candidate_code, c.full_name, c.job.title if c.job_id else '',
                       row.changed_at.strftime('%Y-%m-%d'), rejection_stage(c) or 'Not recorded',
                       row.remarks or '', R.LABELS[code], R.LABELS[code]])
        lists = wb.create_sheet('Reasons')
        for _, label in R.ALL_CHOICES:
            lists.append([label])
        last = ws.max_row
        if last > 1:
            dv = DataValidation(type='list', formula1=f'=Reasons!$A$1:$A${len(R.ALL_CHOICES)}', allow_blank=False)
            ws.add_data_validation(dv)
            dv.add(f'I2:I{last}')
        for col, width in zip('ABCDEFGHI', (11, 15, 26, 30, 12, 22, 60, 36, 36)):
            ws.column_dimensions[col].width = width
        ws.freeze_panes = 'A2'
        wb.save(path)
        total = sum(counts.values())
        self.stdout.write(f'{total} rejection(s) without a reason written to {path}')
        for code, label in R.ALL_CHOICES:
            if counts.get(code):
                self.stdout.write(f'  {counts[code]:>4}  {label}')

    def _apply(self, path, dry_run):
        import openpyxl

        try:
            ws = openpyxl.load_workbook(path, read_only=True).worksheets[0]
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f'Could not read {path}: {exc}')
        rows = list(ws.iter_rows(values_only=True))
        if not rows or list(rows[0][:len(HEADERS)]) != HEADERS:
            raise CommandError('This is not a review sheet made by --export (headers differ).')
        by_label = {label.lower(): code for code, label in R.ALL_CHOICES}
        updates, problems = [], []
        for n, values in enumerate(rows[1:], start=2):
            pk, reason = values[0], (values[8] or '').strip() if isinstance(values[8], str) else values[8]
            code = by_label.get(str(reason or '').strip().lower())
            if not pk:
                continue
            if code is None:
                problems.append(f'row {n}: "{reason}" is not one of the reasons')
                continue
            updates.append((int(pk), code))
        if problems:
            raise CommandError('Fix these rows first:\n  ' + '\n  '.join(problems))
        saved = 0
        with transaction.atomic():
            for pk, code in updates:
                changed = CandidateStatusHistory.objects.filter(
                    Q(reject_reason__isnull=True) | Q(reject_reason=''),
                    pk=pk, new_status=Candidate.Status.REJECTED)
                saved += changed.count() if dry_run else changed.update(reject_reason=code)
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f'{"Would save" if dry_run else "Saved"} a reason on {saved} of {len(updates)} rejection(s).'
                          + (' (rows that already had a reason were left alone)' if saved < len(updates) else ''))
