# HireB

Django + Bootstrap 5 Applicant Tracking System.

**Status:** Phase 1 (public Careers portal) and Phase 2 (HR admin side) are both done:

- Public: open vacancies, job detail popup, application form (with education/experience, resume upload,
  duplicate/blacklist detection via `email_registry`).
- HR admin: Dashboard (KPIs + upcoming interviews), Vacancy Management (create/edit/close/archive/reopen + JD
  upload), Candidate Repository (8-stage pipeline with search/filters), Candidate Timeline (full history, notes,
  communication log, attachments, offers, SLA per stage), Interview Scheduler (schedule/reassign/reschedule/mark
  result/send invite), Bulk Upload CV, Reports (time to hire, vacancy fill rate, rejection ratio, source
  effectiveness), and RBAC (HR Admin / Recruiter / Interviewer / Hiring Manager groups).

Not wired up (need external credentials you'd have to provide): **resume parsing** (would need an LLM/OpenAI API
key) and **real email delivery** (needs SMTP credentials — invites currently print to the console via Django's
console email backend, see `EMAIL_BACKEND` in settings).

The previous single-table "Central Repository" version of this app is preserved in git history
(commit "Snapshot: single-table Central Repository recruitment portal (pre-ATS redesign)") if you need to refer back to it.

## Setup

```
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- Public site: `http://127.0.0.1:8000/` (redirects to `/careers/`)
- HR admin: log in at `/login/`, then `/hr/dashboard/`. `/admin/` (Django admin) also works for raw data management.
- Four groups (`HR Admin`, `Recruiter`, `Interviewer`, `Hiring Manager`) are created automatically after `migrate`;
  assign users to them from `/admin/`. Superusers bypass group checks entirely.

## Data model

- `jobs.Job` — vacancy postings (`job_code` auto-generated, `status` Open/Closed, `is_archived`, `jd_file`).
- `candidates.Candidate` — the single master candidate table; status is a column (`Open, Shortlisted, Round1,
  Interview, FinalSelection, Hired, Rejected, Blacklisted`), never a separate table per stage.
- `candidates.CandidateEducation` / `CandidateExperience` — one-to-many detail rows per candidate.
- `candidates.CandidateStatusHistory` — full audit trail of every status change (also powers the SLA-per-stage view).
- `candidates.Blacklist` — reason/who/when for blacklisted candidates (in addition to the `is_blacklisted` flag).
- `candidates.EmailRegistry` — one row per applicant email, used for duplicate/reapply detection
  (`candidates/services.py::check_email`).
- `candidates.Note` / `CommunicationLog` / `Attachment` / `Offer` — Candidate Timeline extras.
- `candidates.BulkUploadBatch` / `BulkUploadItem` — staging rows for Bulk Upload CV (see below); a Candidate
  is created only once its CV has been parsed successfully.
- `Candidate.role_applied` — the position the applicant *asked for*, as written on their CV or in the
  application email. `Candidate.job` is where they were *filed*, which falls back to the
  **General Application** vacancy when the text matches no open vacancy. The **General Applications**
  page lists that bucket and filters it by `role_applied`.
- `interviews.Interview` — round type, interviewer, schedule, mode, status, result, feedback, score.

## RBAC

`candidates/permissions.py` defines the four groups and a `GroupRequiredMixin` used across `jobs`, `candidates`,
`interviews`, and `dashboard` views. Vacancy Management (create/edit/close/archive) is HR-Admin-only; Candidate
Repository status actions allow HR Admin/Recruiter/Hiring Manager; interview result entry allows HR Admin/Interviewer.

## Bulk Upload CV (CV parsing)

Bulk-uploaded CVs are read by the **`cv-parse-single` Azure Logic App** — the same
Form Recognizer (`MyCVModel`) + Azure OpenAI (`cv-data-agent`) extraction the careers-mailbox
intake flow uses — so they arrive with name, email, phone, education and an AI summary
instead of a name guessed from the filename.

```
Upload screen  ->  BulkUploadItem per file  ->  background worker  ->  Logic App (one CV per call)
                                                      |
                                     parsed  ->  Candidate + CandidateEducation (status Open)
                                     failed  ->  error on the results screen, no candidate row
```

The results screen shows live progress and then "N uploaded successfully, M errors", listing
each success (with a link to the candidate) and each failure (with the reason and a **Retry**
button that re-uses the stored file). The vacancy and source picked on the upload screen always
win over whatever the CV says.

Set `LOGIC_APP_CV_PARSER_URL` (plus the optional `CV_PARSER_*` / `BULK_UPLOAD_*` settings) to
enable it — **[logic_apps/README.md](logic_apps/README.md)** has the workflow JSON and the
one-time Azure setup. Without it the app runs normally; bulk uploads just report that parsing
is not configured.

Tests: `DB_ENGINE=sqlite python manage.py test candidates` (never runs against the Azure DB).

## Switching to PostgreSQL / Azure SQL

SQLite is used by default. Set `DB_ENGINE=postgres` plus `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
(env vars) to point at Postgres; swap the `ENGINE` in `HR_management/settings.py` for an Azure-SQL-compatible
backend (e.g. `mssql-django`) the same way. Resumes are stored locally under `media/cvs/<job_id>/<candidate_code>/`;
swapping `DEFAULT_FILE_STORAGE` to `django-storages`' Azure Blob backend later needs no model changes.
