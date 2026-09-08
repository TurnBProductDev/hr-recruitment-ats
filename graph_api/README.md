# Microsoft Graph API (Phase 2 of interview scheduling)

Phase 1 (shipped) stops the ATS from double-booking the same interviewer
into two overlapping slots *within the app itself* - see
`Interview.conflicts_for()` in `interviews/models.py`. It cannot see
anything on an interviewer's actual Outlook calendar, and `meeting_link`
is still a plain URL field HR pastes a Teams link into by hand.

Phase 2 closes both gaps through the Microsoft Graph API:
- auto-create a real Teams meeting and fill `meeting_link` automatically
  when HR schedules an interview.
- check the interviewer's real Outlook free/busy before allowing the
  schedule, not just other interviews already in this database.

This needs setup in Azure AD/Teams admin **before any code here can work**,
tracked in this doc the same way `logic_apps/README.md` tracks the Logic
Apps setup, so it can be followed step by step instead of trusted to memory.

## 1. Register an app in Azure AD

Needs Application Administrator (or Global Admin) on the TurnB tenant.

1. Azure Portal -> **Azure Active Directory** -> **App registrations** ->
   **New registration**.
2. Name: e.g. `HireB Interview Scheduling`.
3. Supported account types: **Single tenant** (TurnB only).
4. No redirect URI needed - this app never signs a user in. It calls
   Graph as itself (client-credentials flow), the same shape as the Logic
   Apps' service connections, not a user-facing OAuth app.
5. After creation, copy from the **Overview** page:
   - **Application (client) ID** -> `GRAPH_CLIENT_ID`
   - **Directory (tenant) ID** -> `GRAPH_TENANT_ID`

## 2. Create a client secret

1. The app registration -> **Certificates & secrets** -> **New client
   secret**.
2. Copy the secret's **Value** immediately - it is shown once. This is
   `GRAPH_CLIENT_SECRET`. Treat it exactly like `AZURE_OPENAI_KEY`: never
   commit it, only ever set it as an environment variable.

## 3. Grant API permissions

1. The app registration -> **API permissions** -> **Add a permission** ->
   **Microsoft Graph** -> **Application permissions** (not Delegated -
   Django runs unattended, there is no signed-in user).
2. Add:
   - `OnlineMeetings.ReadWrite.All` - create Teams meetings.
   - `Calendars.ReadWrite` - read the interviewer's free/busy, and (later,
     optional) write the event straight onto their calendar instead of
     only emailing an `.ics`.
3. Click **Grant admin consent for TurnB**. This button only works for a
   Global Admin or Privileged Role Administrator - if that is not you,
   this is the step to hand off.

## 4. Teams application access policy

This is the step every quick guide skips, and the reason a correctly
permissioned app can still fail to create a meeting: creating an online
meeting "on behalf of" a mailbox requires that mailbox to explicitly
trust this specific app. Application permissions alone are not enough.

Recommended: always organize interview meetings as **one fixed mailbox**
(`careers@turnb.com`) rather than as each individual interviewer. That
avoids re-granting this policy every time a new interviewer is added to
the ATS.

A Teams Admin runs (Teams PowerShell module):

```powershell
Connect-MicrosoftTeams

New-CsApplicationAccessPolicy `
    -Identity HireBSchedulingPolicy `
    -AppIds "<GRAPH_CLIENT_ID from step 1>" `
    -Description "Allow the HireB ATS to create Teams meetings"

Grant-CsApplicationAccessPolicy `
    -PolicyName HireBSchedulingPolicy `
    -Identity careers@turnb.com
```

This grants the app permission to organize meetings as `careers@turnb.com`
specifically - not every mailbox in the tenant.

## 5. Hand over the three values

- `GRAPH_TENANT_ID`
- `GRAPH_CLIENT_ID`
- `GRAPH_CLIENT_SECRET`

Set them as environment variables (local `.env` for testing, Azure Web
App -> Settings -> Environment variables for production) - same handling
as `AZURE_OPENAI_KEY`. Also set:

- `GRAPH_ORGANIZER_EMAIL=careers@turnb.com` - the mailbox from step 4 that
  organizes every Teams meeting.

## What gets built once the above exists

- `interviews/graph_client.py` - client-credentials token fetch
  (`POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token`),
  cached until shortly before it expires.
- A free/busy check (`POST /users/{GRAPH_ORGANIZER_EMAIL}/calendar/getSchedule`
  for the interviewer's email) added to `InterviewForm.clean()` alongside
  the Phase 1 in-app conflict check, so a real Outlook clash is caught the
  same way a same-app clash already is.
- `POST /users/{GRAPH_ORGANIZER_EMAIL}/onlineMeetings` when an interview is
  scheduled, to fill `meeting_link` automatically with a real Teams join
  URL instead of HR typing one in.
- Gated behind an `is_configured()` check, the same pattern
  `candidates/match_scoring.py` and `candidates/cv_parser.py` already use -
  so it ships inert (falls back to today's manual-link behaviour) until the
  three settings above are actually set, and turns on the moment they are,
  with no code change or redeploy needed.
