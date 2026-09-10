# Logic Apps

Workflows in the **HRMS** resource group that feed candidates into the ATS,
tracked here so the prompt/config live in Azure can be diffed against git
instead of trusted from memory:

| Workflow (Azure name) | File | Trigger | Writes candidates via | Status |
|---|---|---|---|---|
| `CV-Automation-Flow` | [`mailbox_intake.json`](mailbox_intake.json) | New email in the careers inbox | `sp_intake_add_candidate` (stored proc) | **Live** |
| `cv-parse-single` | [`cv_parse_single.json`](cv_parse_single.json) | HTTP POST from the ATS **Bulk Upload CV** screen | Django writes the row itself | **Live** (SharePoint filing only - see below) |
| `CV-Automation-Flow-Final` | [`cv_automation_flow_final.json`](cv_automation_flow_final.json) | New email in the careers inbox (same mailbox) | `sp_intake_add_candidate`, extended params | **Disabled** - being built as `CV-Automation-Flow`'s eventual replacement, see below |

`mailbox_intake.json` (the live `CV-Automation-Flow`) is **untouched** by any
of this - the rebuild happens entirely in `CV-Automation-Flow-Final`, which
stays Disabled until it's been verified end-to-end against real test emails.
Only once that's confirmed working does `CV-Automation-Flow-Final` get
Enabled and `CV-Automation-Flow` get Disabled - never both changed at once.

## The CV-reading rebuild (Bulk Upload done, mailbox intake pending)

Both intake pipelines used to read CVs the same way: Form Recognizer's
**`MyCVModel`** custom model (labelled only for `NAME`, `EMAIL`, `MOBILE NO`,
`EDUCATION`) feeding two separate Azure OpenAI completions (a narrow field
extraction, then a summary). That's replaced by `candidates/cv_extraction.py`,
which reads the PDF directly (its own text layer, or page images as a
fallback for a scanned CV) and asks Azure OpenAI for *every* field - including
Skills and a structured Experience list, which the old pipeline never
captured at all - in one call. See that module's docstring for the full
reasoning.

- **`cv-parse-single` / Bulk Upload CV**: done. Django calls
  `cv_extraction.py` directly (no Logic App round trip for reading); the
  Logic App now only files the CV to SharePoint and returns the link.
- **`CV-Automation-Flow` / mailbox intake**: pending, via
  `CV-Automation-Flow-Final`. Since this path has no Django code in the
  loop before the candidate is created (it's a pure Logic-App-to-SQL flow),
  the rebuild adds one new piece: **`candidates.views.CVExtractAPIView`**
  (`POST /api/cv/extract/`, key-authenticated via `X-Api-Key` /
  `CV_EXTRACT_API_KEY` - treat it as a password) - a thin HTTP wrapper around
  `cv_extraction.py` that a Logic App can call. `CV-Automation-Flow-Final`
  POSTs the attachment + email Subject/Body there instead of running
  `AnalyzeCV`/`Extract_completion`/`Summary_completion` itself, then calls
  the extended `sp_intake_add_candidate` (see `sql/sp_intake_add_candidate.sql`)
  with everything the API returned. Everything else in the original flow -
  the sender/subject exclusion filter, the cover-letter filename filter, the
  SharePoint + Blob dual-storage upload, and the Excel-based recruitment-
  agency source lookup - is unchanged.

`sp_intake_add_candidate`'s new parameters (skills, linkedin, current_location,
dob, last_role, last_company, total_experience_years, notice_period,
expected/current salary, and an `experience_json` array for structured
`CandidateExperience` rows) are all optional and default to `NULL` - the live
`CV-Automation-Flow` calls the procedure with today's parameter set only and
is unaffected by the extension.

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
link) is Django's job now - see `candidates/cv_extraction.py` and the
`cv-parse-single` section above. To add a new field there, extend its
`RESPONSE_JSON_SCHEMA` + `SYSTEM_PROMPT` and map the result in
`candidates/services.py::create_from_parsed_cv`; nothing here needs to change.

The careers-mailbox intake workflow (`mailbox_intake.json`) still reads CVs
the old way - Form Recognizer's **`MyCVModel`** custom model, labelled only for
`NAME`, `EMAIL`, `MOBILE NO`, `EDUCATION` - pending the same move to
`cv_extraction.py` that `cv-parse-single` just got.
