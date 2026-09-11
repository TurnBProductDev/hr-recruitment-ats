# Logic Apps

Workflows in the **HRMS** resource group that feed candidates into the ATS,
tracked here so the prompt/config live in Azure can be diffed against git
instead of trusted from memory:

| Workflow (Azure name) | File | Trigger | Writes candidates via | Status |
|---|---|---|---|---|
| `CV-Automation-Flow-Final` | [`cv_automation_flow_final.json`](cv_automation_flow_final.json) | New email in the careers inbox | `sp_intake_add_candidate`, extended params | **Live** (cut over 2026-09-10) |
| `cv-parse-single` | [`cv_parse_single.json`](cv_parse_single.json) | HTTP POST from the ATS **Bulk Upload CV** screen | Django writes the row itself | **Live** (SharePoint filing only - see below) |
| `CV-Automation-Flow` | [`mailbox_intake.json`](mailbox_intake.json) | New email in the careers inbox (same mailbox) | `sp_intake_add_candidate` (stored proc) | **Disabled** - retired in favour of `CV-Automation-Flow-Final`; kept here/in Azure for reference and rollback, not deleted |

Both intake paths now read CVs the same way - Form Recognizer has no callers
left in either one.

## The CV-reading rebuild (complete on both paths)

Both intake pipelines used to read CVs the same way: Form Recognizer's
**`MyCVModel`** custom model (labelled only for `NAME`, `EMAIL`, `MOBILE NO`,
`EDUCATION`) feeding two separate Azure OpenAI completions (a narrow field
extraction, then a summary). That's replaced by `candidates/cv_extraction.py`,
which reads the PDF directly (its own text layer, or page images as a
fallback for a scanned CV) and asks Azure OpenAI for *every* field - including
Skills and a structured Experience list, which the old pipeline never
captured at all - in one call. See that module's docstring for the full
reasoning.

- **`cv-parse-single` / Bulk Upload CV**: Django calls `cv_extraction.py`
  directly (no Logic App round trip for reading); the Logic App only files
  the CV to SharePoint and returns the link.
- **`CV-Automation-Flow-Final` / mailbox intake**: since this path has no
  Django code in the loop before the candidate is created (it's a pure
  Logic-App-to-SQL flow), the rebuild added one new piece:
  **`candidates.views.CVExtractAPIView`** (`POST /api/cv/extract/`,
  key-authenticated via `X-Api-Key` / `CV_EXTRACT_API_KEY` - treat it as a
  password) - a thin HTTP wrapper around `cv_extraction.py` that the Logic App
  calls. It POSTs the attachment + email Subject/Body there instead of
  running `AnalyzeCV`/`Extract_completion`/`Summary_completion` itself, then
  calls the extended `sp_intake_add_candidate` (see
  `sql/sp_intake_add_candidate.sql`) with everything the API returned.
  Everything else from the original flow - the sender/subject exclusion
  filter, the cover-letter filename filter, the SharePoint + Blob
  dual-storage upload, and the Excel-based recruitment-agency source lookup -
  is unchanged.

`sp_intake_add_candidate`'s new parameters (skills, linkedin, current_location,
dob, last_role, last_company, total_experience_years, notice_period,
expected/current salary, and an `experience_json` array for structured
`CandidateExperience` rows) are all optional and default to `NULL` - this kept
`CV-Automation-Flow` working unchanged for as long as it stayed live, calling
the procedure with only its original parameter set.

### `djangoApiKey` is never committed here - it's blank in this file on purpose

`cv_automation_flow_final.json`'s top-level `parameters.djangoApiKey.value` is
always `""` in this repo. The real value only lives in two places: the
`CV_EXTRACT_API_KEY` App Service setting, and the Logic App's own live
parameter in Azure (set once via the CLI, never through this file). If you
redeploy this JSON via `az logic workflow update`, **first patch a real key
into a local copy** - deploying the checked-in blank value breaks
`CV-Automation-Flow-Final` by wiping the live key. (A key was accidentally
committed here in plaintext once, in commits `9027aed`/`f9e5d82` - it was
rotated on 2026-09-11 and that old value is dead. Don't repeat that: never put
the real value back in this file.)

### Deploying changes to `CV-Automation-Flow-Final` - use the CLI, not Code View

The Portal's Logic app designer Code View **silently drops the request body**
when you paste in a hand-written `Execute stored procedure (V2)` action whose
parameters don't match what the connector's own cached schema expects - it
saves without any error, but the body is just gone (confirmed by reading the
saved definition back). Pushing the same JSON via the CLI instead works
correctly:

```
az logic workflow update --resource-group HRMS --name CV-Automation-Flow-Final \
  --definition @logic_apps/cv_automation_flow_final.json
```

Two other traps hit while building this, worth knowing if you touch this
workflow again:
- **`content_base64` must NOT be wrapped in `base64(...)`.** Office 365's
  "Get attachment" action already returns `contentBytes` as base64 - encoding
  it again produces a value that only decodes to *another* base64 string, not
  the PDF, and the model then reads nothing. Pass `contentBytes` straight
  through.
- **`createArray()` needs at least one argument.** `coalesce(x, createArray())`
  fails with `InvalidTemplate` at runtime, not at save time. Use `json('[]')`
  for an empty-array fallback instead.
- **`full_name` and `email` have no default in the stored procedure** (every
  other new parameter does) - if a CV genuinely doesn't state them, the
  insert fails outright. Both are wrapped in `coalesce(...)` with a fallback
  (the attachment filename for the name, a `pending-<guid>@placeholder.local`
  address for email, matching `candidates.models.PLACEHOLDER_EMAIL_DOMAIN`)
  rather than left to fail.

## `cv-parse-single`

Files **one** CV to SharePoint and returns its share link. Used by the ATS
**Bulk Upload CV** screen.

CV *reading* (Name/Email/Skills/Experience/Summary/...) no longer happens
here - it moved to `candidates/cv_extraction.py`, which reads the PDF's own
text layer directly (falling back to page images for a scanned CV) and asks
Azure OpenAI for every field in one call, instead of Form Recognizer's 4
labelled fields (`MyCVModel`) plus two separate Logic-App completions. See
that module's docstring for why. This workflow now only exists to keep the
SharePoint filing step in one place, since Django doesn't yet talk to
SharePoint directly.

```
Django (bulk upload)  --reads the PDF itself, via cv_extraction.py-->  every candidate field
                       --POST one CV-->  cv-parse-single  --{CV_Link}-->  Django
                                             |
                                             +-- SharePoint upload + sharing link
```

The careers intake workflow (email trigger → `sp_intake_add_candidate`,
tracked as [`mailbox_intake.json`](mailbox_intake.json)) is **unchanged** by
this one - see "Extending the extracted fields" below for the plan to bring
it onto the same `cv_extraction.py` pipeline.

Django writes the candidate row itself (it already knows the vacancy and source
the HR user picked), so this workflow does **not** call the stored procedure.
A missing/failed SharePoint filing is a warning on the results screen, not a
failed upload - CV reading and CV filing are independent now.

## Create the workflow

1. Azure portal → resource group **HRMS** → **Create** → *Logic App* →
   **Consumption**, region **Central India**, name `cv-parse-single`.
2. Open it → **Logic app designer** → start with **Blank Logic App** →
   switch to **Code view**.
3. Paste the entire contents of [`cv_parse_single.json`](cv_parse_single.json)
   over what is there → **Save**.
4. The designer will show the connections as needing authorisation the first
   time. Open each action (`AnalyzeCV`, `Extract_completion`,
   `Summary_completion`, `Create_file_1`, `Create_sharing_link…`) and pick the
   existing connection from the dropdown — they are the same ones the intake
   flow uses (`formrecognizer`, `azureopenai`, `sharepointonline-2`). Save again.
5. Open the trigger **When a HTTP request is received** and copy the
   **HTTP POST URL**. It contains a SAS signature — treat it as a password.

## Point the app at it

Set the URL as an app setting (Azure portal → the ATS Web App → Settings →
Environment variables), and in your local `.env` for testing:

```
LOGIC_APP_CV_PARSER_URL=https://prod-XX.centralindia.logic.azure.com:443/workflows/..../triggers/When_a_HTTP_request_is_received/paths/invoke?api-version=2016-10-01&sp=...&sv=...&sig=...
```

Optional settings (defaults in brackets):

| Setting | Purpose |
|---|---|
| `CV_PARSER_TIMEOUT` | seconds to wait for one CV [180] |
| `CV_PARSER_UPLOAD_TO_SHAREPOINT` | `True`/`False` — copy the CV to SharePoint [True] |
| `BULK_UPLOAD_MAX_FILES` | files per batch [25] |
| `BULK_UPLOAD_MAX_MB` | max size per file [10] |

If `LOGIC_APP_CV_PARSER_URL` is unset, bulk upload still works — every file is
recorded as an error with "CV parsing is not configured", and no candidate rows
are created. Nothing else in the app is affected.

## Request / response contract

Request body (unchanged from before, so Django's client didn't need to change
its call shape - `role_hint`/`source_hint` are accepted but no longer used
for anything, since this workflow doesn't read the CV anymore):

```json
{
  "filename": "ID202600123_John_Doe.pdf",
  "content_base64": "<base64 of the file>",
  "role_hint": "Data Analyst",
  "source_hint": "Naukri",
  "upload_to_sharepoint": true
}
```

Success response (HTTP 200):

```json
{ "status": "ok", "CV_Link": "https://netorg519925.sharepoint.com/…" }
```

`CV_Link` is `""` when `upload_to_sharepoint` was `false` - that's still
`status: ok`, there was just nothing to file. Django turns a missing link into
a warning on the results screen, not an error, same as before.

Failure response is **also HTTP 200** so the app can show a readable reason
instead of a raw gateway error:

```json
{ "status": "error", "action": "Create_file_1", "message": "…" }
```

If a CV takes longer than the Request trigger's synchronous window, Azure
answers **202 Accepted** with a `Location` header instead. The Django client
polls that URL until the run finishes, so slow uploads are not lost.

## Why the graph looks like this

**Success is decided by whether a link came back, not by the scope status.**
If `upload_to_sharepoint` was requested and `Should_upload_to_SharePoint`'s
actions produced no link (`Create_file_1` or the sharing-link step failed),
`Respond` takes the else branch and returns `status: error` with the first
failed action's message, instead of a misleadingly-`ok` empty response.

## Extending the extracted fields

CV *reading* (Skills, Experience, Summary, everything beyond a SharePoint
link) is Django's job now, for both intake paths - see
`candidates/cv_extraction.py`. To add a new field, extend its
`RESPONSE_JSON_SCHEMA` + `SYSTEM_PROMPT`, map the result in
`candidates/services.py::create_from_parsed_cv` (Bulk Upload), and add the
matching parameter to `sql/sp_intake_add_candidate.sql` + the two
`Execute stored procedure` action bodies in `cv_automation_flow_final.json`
(mailbox intake) - deployed via the CLI command above, not Code View.

Form Recognizer (`MyCVModel`) has no callers left in either workflow as of
the 2026-09-10 cutover.
