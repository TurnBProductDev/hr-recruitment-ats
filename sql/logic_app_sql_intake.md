# Careers Logic App → Azure SQL (Open Candidates)

Goal: keep every existing step (email trigger, CV parsing, Azure OpenAI extraction,
SharePoint upload + share link, agency-source lookup) and only change the **final
sink** from "Add a row to the Central Repository Excel" to "insert into the ATS
database", so new applicants appear in the **Open Applications** tab.

## What replaces what

The two Excel actions inside `Condition_2` — `Add_a_row_into_a_table_1` (agency
branch) and `Add_a_row_into_a_table_2` (else branch) — are each replaced by a
**SQL Server → Execute stored procedure (V2)** action calling
`dbo.sp_intake_add_candidate`. Everything above them is unchanged.

The stored procedure (see `sp_intake_add_candidate.sql`) does what the web app does:
generates the candidate code, resolves the vacancy from the role text, flags
duplicates/blacklist, updates the email registry, and writes the initial
`Applied` (status = OPEN) history row.

## One-time setup

1. **Deploy the procedure** (already applied to `hr_recruitment`). To redeploy, run
   `sql/sp_intake_add_candidate.sql` against the DB (SSMS / Azure Query editor).

2. **Add a SQL Server connection to the Logic App**
   - Logic App → the intake workflow → add/replace an action → search **SQL Server**
     → **Execute stored procedure (V2)** → *Create new connection*:
     - Authentication: **SQL Server Authentication**
     - Server: `hr-turnb-sql02.database.windows.net` (turnb.com tenant, since
       2026-10-23 - was `hr-turnb-sql01` in the old turnretail tenant)
     - Database: `hr_recruitment`
     - Username: `hrmsadmin`   Password: *(the DB password - same as the web
       app's `DB_PASSWORD` setting)*
   - Ensure the SQL server firewall allows Azure services (Networking →
     "Allow Azure services and resources to access this server" = Yes).

## Designer steps (recommended over editing JSON)

Inside `Condition_2`, in **each** branch:
1. Delete the `Add a row into a table` (Excel) action.
2. Add **SQL Server – Execute stored procedure (V2)**.
3. Procedure name: `[dbo].[sp_intake_add_candidate]`.
4. Map the parameters:

| SP parameter | Value (dynamic content / expression) |
|---|---|
| `full_name`   | `body('Parse_JSON')?['Name']` |
| `email`       | `body('Parse_JSON')?['Email']` |
| `phone`       | `body('Parse_JSON')?['Mobile']` |
| `role_applied`| `body('Parse_JSON')?['Role_Applied']` |
| `education`   | `body('Parse_JSON')?['Education']` |
| `cv_link`     | `body('Create_sharing_link_for_a_file_or_folder')?['link']?['webUrl']` |
| `source`      | **agency (if) branch:** `first(body('Filter_array'))?['Agency Name']` · **else branch:** `body('Parse_JSON')?['Source']` |
| `mail_date`   | `formatDateTime(triggerOutputs()?['body/receivedDateTime'], 'yyyy-MM-dd')` |

(The only difference between the two branches is the `source` value — same as the
old Excel actions.)

## Code view equivalent (agency branch shown)

```json
"Insert_candidate_into_SQL": {
  "type": "ApiConnection",
  "inputs": {
    "host": {
      "connection": { "name": "@parameters('$connections')['sql']['connectionId']" }
    },
    "method": "post",
    "path": "/v2/datasets/@{encodeURIComponent(encodeURIComponent('default'))},@{encodeURIComponent(encodeURIComponent('default'))}/procedures/@{encodeURIComponent(encodeURIComponent('[dbo].[sp_intake_add_candidate]'))}",
    "body": {
      "full_name": "@body('Parse_JSON')?['Name']",
      "email": "@body('Parse_JSON')?['Email']",
      "phone": "@body('Parse_JSON')?['Mobile']",
      "role_applied": "@body('Parse_JSON')?['Role_Applied']",
      "education": "@body('Parse_JSON')?['Education']",
      "cv_link": "@body('Create_sharing_link_for_a_file_or_folder')?['link']?['webUrl']",
      "source": "@first(body('Filter_array'))?['Agency Name']",
      "mail_date": "@formatDateTime(triggerOutputs()?['body/receivedDateTime'], 'yyyy-MM-dd')"
    }
  },
  "runAfter": { "Filter_array": ["Succeeded"] }
}
```

For the **else** branch, use the same action but
`"source": "@body('Parse_JSON')?['Source']"`.

Add the connection to the workflow's `$connections` parameters, e.g.:

```json
"sql": {
  "id": "/subscriptions/32ea51d0-6a7d-4a16-ab91-4f5f80f708c7/providers/Microsoft.Web/locations/centralindia/managedApis/sql",
  "connectionId": "/subscriptions/32ea51d0-6a7d-4a16-ab91-4f5f80f708c7/resourceGroups/HRMS/providers/Microsoft.Web/connections/sql",
  "connectionName": "sql"
}
```
(The exact `connectionId` is filled in for you when you create the connection in the designer.
The `default`,`default` dataset in the path above means "the server and database
stored on the connection" - which is what the live workflow uses, so moving the
database only means editing the `sql` connection, not the workflow.)

## Applied position (2026-08-10)

`@role_applied` used to be read only to *resolve* the vacancy and was then thrown
away, so anyone who landed in **General Application** lost the position they had
actually asked for. The proc now also stores it in the new
`candidates_candidate.role_applied` column, which powers the **General
Applications** page in the app.

**Deploy order matters:** ship the Django app first (the container runs
`manage.py migrate` on boot, creating the column), *then* re-run
`sp_intake_add_candidate.sql`. Applying the proc first makes every insert fail on
the missing column. No Logic App change is needed — it already passes
`role_applied`.

Rows created before this change keep `role_applied = NULL` and show as
"Not captured"; the original text was never stored anywhere, so there is nothing
to backfill from.

## Behaviour notes

- **Role → vacancy:** matched on exact title; anything unmatched lands under
  *General Application* (so the record is never lost). Add/rename vacancies in the
  app to capture more roles automatically.
- **Duplicates:** a repeat email is inserted as a new Open application and flagged
  `Reapply` (same as the app), and the email registry count increments.
- **Excel is no longer written.** If you want a transition period, you can keep both
  the Excel action and the SQL action in each branch.
- **Test:** send a test application email; the candidate should appear in
  Candidates → Open Applications within a minute, with the CV link and source set.
