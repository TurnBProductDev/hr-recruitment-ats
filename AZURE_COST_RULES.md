# Azure cost rules — read before any command that could touch Azure

This project's local `.env` points at the **live Azure SQL database**
(`DB_ENGINE=azure`, server `hr-turnb-sql02`, resource group `HRMS`,
subscription "HR and Admin Team"). A wrong command on this machine can create
billed Azure resources without any portal click. This has already happened
twice. These rules exist so it never happens again.

## Rule 1 — Never create an Azure resource without the owner's approval

Before creating, provisioning, restoring, copying or scaling up **anything**
that costs money in Azure (SQL database, App Service, VM, storage account,
Logic App, container registry, Power BI capacity, elastic pool, …) — whether
by `az` CLI, ARM/Bicep, T-SQL, Django, a script or any other tool — **stop and
ask first**. The request must state:

- **What:** resource type, name, SKU/tier, region
- **Billing model:** serverless/auto-pausing, or provisioned/always-on
- **Cost:** hourly or daily rate, and a rough monthly estimate
- **Why** it is needed, and **when it will be deleted**

Do not proceed until the owner explicitly says yes. "Temporary" and "test"
resources are not exempt; they are exactly what went wrong before. If the
resource is one step in a bigger task, stop at that step and ask on its own.
Don't fold it into a general "does this plan look good?".

## Rule 2 — Delete what you create, and say so

If an approved resource was created for a one-off purpose, delete it when the
purpose is done. Then confirm with `az … list` that it is gone, and tell the
owner. Never leave a resource running silently.

## Rule 3 — Commands on this machine that can create Azure resources

| Command / action | What it can create | Safe way |
|---|---|---|
| `python manage.py test` | A `test_hr_recruitment` database on Azure SQL (Azure makes it General Purpose 2 vCore, about Rs.1,450/day) | `settings.py` now forces SQLite for `test`; check that the guard is still there, and prefix `DB_ENGINE=sqlite` anyway |
| T-SQL `CREATE DATABASE`, `… AS COPY OF …` | A new provisioned database | Ask first (Rule 1) |
| `az sql db create / copy / restore`, point-in-time restore | A new database | Ask first (Rule 1) |
| `az … create`, `az … update --sku/--tier/--capacity` | Any resource / a higher tier | Ask first (Rule 1) |
| Deploying ARM/Bicep templates, or pushing a workflow that does | Whatever the template contains | Ask first (Rule 1) |

Running SQL *inside* `hr_recruitment` (SELECT, UPDATE, `CREATE OR ALTER
PROCEDURE`) does not create a billed resource, but it changes live data, so
treat it with the usual care.

## Rule 4 — If something *might* have created a resource, check properly and report at once

- Check with the Azure CLI:
  `az sql db list --subscription "HR and Admin Team" -g HRMS -s hr-turnb-sql02 -o table`
  The only databases there should be `master` and `hr_recruitment` (Basic tier).
- **Do not** rely on `SELECT … FROM sys.databases` run from inside
  `hr_recruitment`. On Azure SQL that lists only `master` and the current
  database, so it will not show a stray test database. This false all-clear
  is how the 2026-11-12 database went unnoticed.
- If anything unexpected exists, tell the owner straight away. Say what it is,
  when it was created and the estimated cost so far. Delete it only after
  checking it holds no real data.

## Incident log

| Date | What happened | Cost / duration | Fix |
|---|---|---|---|
| 2026-09-09 | An empty `test_hr_recruitment` database was created on Azure SQL without approval and billed as provisioned | about Rs.1,450/day until caught | Owner added the global "confirm cost first" rule |
| 2026-11-12 | A Claude session ran `manage.py test` against Azure SQL. The run timed out and left `test_hr_recruitment` (General Purpose, 2 vCore) running. Its `sys.databases` check gave a false all-clear and the owner was not told | 13 days (until 2026-11-25), about Rs.19,000 estimated | Database deleted; `settings.py` now forces SQLite for tests (commit `783b3b2`); this file added |
