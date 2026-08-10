# Logic App: `cv-parse-single`

Parses **one** CV on demand and returns the extracted fields as JSON. Used by the
ATS **Bulk Upload CV** screen, so bulk-uploaded CVs get the same email / phone /
education / summary extraction that the careers-mailbox intake flow already does.

The careers intake workflow (email trigger → `sp_intake_add_candidate`) is
**unchanged**. This is a second, separate workflow that shares the same
Form Recognizer, Azure OpenAI and SharePoint connections.

```
Django (bulk upload)  --POST one CV-->  cv-parse-single  --JSON-->  Django
                                             |
                                             +-- AnalyzeCV (MyCVModel)
                                             +-- Extract_completion (cv-data-agent)
                                             +-- Summary_completion (cv-data-agent)
                                             +-- SharePoint upload + sharing link
```

Django writes the candidate row itself (it already knows the vacancy and source
the HR user picked), so this workflow does **not** call the stored procedure.

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

Request body:

```json
{
  "filename": "ID202600123_John_Doe.pdf",
  "content_base64": "<base64 of the file>",
  "role_hint": "Data Analyst",
  "source_hint": "Naukri",
  "upload_to_sharepoint": true
}
```

`role_hint` / `source_hint` are the vacancy title and source the HR user picked
on the upload screen; they stand in for the email subject the intake flow reads.
Django overrides `Role_Applied` and `Source` with the HR picks anyway — the hints
only steer the model.

Success response (HTTP 200):

```json
{
  "status": "ok",
  "Name": "John Doe",
  "Email": "john.doe@example.com",
  "Mobile": "+91-9876543210",
  "Education": "MBA - DC School of Management & Technology - 2019",
  "Role_Applied": "Data Analyst",
  "Source": "Careers",
  "Summary": "…4-5 sentences…",
  "CV_Link": "https://netorg519925.sharepoint.com/…"
}
```

Failure response is **also HTTP 200** so the app can show a readable reason
instead of a raw gateway error:

```json
{ "status": "error", "action": "AnalyzeCV", "message": "…" }
```

`Summary` or `CV_Link` can come back empty on an otherwise successful parse (the
summary or the SharePoint upload failed). That is not an error — the app records
the candidate and flags a warning on the results screen.

If a CV takes longer than the Request trigger's synchronous window, Azure
answers **202 Accepted** with a `Location` header instead. The Django client
polls that URL until the run finishes, so slow CVs are not lost.

## Differences from the intake workflow (deliberate)

1. **Summary input.** The intake flow feeds the summary model
   `base64ToString(contentBytes)` — the raw PDF bytes decoded as text, which is
   mostly binary noise. Here the summary is built from
   `body('AnalyzeCV')?['analyzeResult']?['content']`, the text Form Recognizer
   already extracted. **Worth porting back into the intake flow** — it is a
   one-expression change to `Summary_completion`.
2. **Retry policy** trimmed from `count 5 / PT20S` to `count 2 / PT10S` on both
   OpenAI calls, so one CV finishes inside the synchronous response window.
3. **Error handling.** See below — success is decided by the data, not by the
   scope's status.
4. **No SP call, no Excel agency lookup.** Vacancy and source come from the
   upload form.

## Why the graph looks like this

Two rules are load-bearing; changing them re-introduces bugs that were found in
testing.

**`Summary_completion` and `Should_upload_to_SharePoint` both run straight after
`Parse_JSON`, in parallel.** The obvious chain — SharePoint after Summary, with
`runAfter: [Succeeded, Failed, TimedOut, Skipped]` so an optional summary can't
block the upload — is a trap. A scope takes its status from its *terminal*
actions, so a last action that runs no matter what makes the scope report
**Succeeded even when `AnalyzeCV` failed**. A junk file then came back as
`{"status":"ok"}` with every field empty in 0.8 s, and the app would have created
a candidate with a placeholder email — exactly the row this design exists to
prevent. Hanging both off `Parse_JSON` also means a file that can't be read is
never uploaded to SharePoint.

**Success is decided by `Extracted_text`, not by the scope status.**
`Extracted_text` concatenates Name + Email + Education; if all three are empty
(actions skipped, or Form Recognizer read nothing) the `Respond` condition takes
the else branch and returns `status: error` with the first failed action's
message. This also keeps the response correct when a *non-essential* step fails:
if only `Summary_completion` fails the scope is Failed, but the fields are there,
so the CV still succeeds with an empty `Summary`. Django turns an empty `Summary`
or `CV_Link` into a warning on the results screen rather than an error.

Note `concat` rather than `coalesce` in `Extracted_text`: `coalesce` only skips
*null*, so an empty-string `Name` would shadow a perfectly good `Email`.

## Extending the extracted fields

The extraction is limited by what **`MyCVModel`** (Form Recognizer custom model)
has labelled: `NAME`, `EMAIL`, `MOBILE NO`, `EDUCATION`. To capture skills,
experience, current company or location, label those fields in `MyCVModel`
first, then add them to the `Extract_completion` system prompt, the `Parse_JSON`
schema and `Response_Success` here, and map them in
`candidates/cv_parser.py::map_to_candidate_fields`.
