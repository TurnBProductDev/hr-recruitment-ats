# Hosting: Azure App Service (container)

Production runs at **https://turnb-ats.azurewebsites.net** as a Linux container on
Azure App Service. Every push to `main` deploys it (see "Deploying" below) - you
normally never run an Azure command to ship code.

It moved from the turnretail tenant to the turnb.com tenant on 2026-10-23; the
old resources (`hr-turnb-sql01`, `turnb-hrms-ats`, `turnbhrmsacr`, `hirebcvfiles`,
...) were deleted the same day, which is why most new names end in `2`/`02`.

## Where everything is

Tenant **turnb.com**, subscription **HR and Admin Team**
(`32ea51d0-6a7d-4a16-ab91-4f5f80f708c7`), resource group **`HRMS`**:

| Resource | Name | SKU / region | Used for |
|---|---|---|---|
| Web app | `turnb-ats` | on plan `ASP-HRMS`, Linux **B1**, Central India | The ATS. Always On, Basic auth publishing ON |
| Container registry | `turnbhrmsacr2` | Basic, Central India | Image `ats:<commit sha>`; task `purge-old-ats-images` keeps the last 20 (Sundays 02:00 UTC) |
| Azure SQL | `hr-turnb-sql02` / `hr_recruitment` | **Basic** (5 DTU, always on), Central India | All ATS data + the intake stored procedures |
| Storage | `turnbhrmscvs` / container `candidate-cvs` | StorageV2 LRS Hot | CVs filed by the careers-mailbox intake |
| Azure OpenAI | `turnb-hrms-openai` | S0, **South India** | Deployments `cv-data-agent` (gpt-4o) and `cv-extraction-mini` (gpt-4.1-mini) |
| Logic Apps | `CV-Automation-Flow-Final`, `cv-parse-single`, `Send-Email-Notifier`, `Interview-Calendar` | Consumption, Central India | See [`logic_apps/README.md`](../logic_apps/README.md) |
| Alerts | action group `Logic Group` + one `Logic Flow Run Fail - <workflow>` per flow | - | Emails dilshad.mn@turnb.com when a flow run fails |
| App registration | `TurnB ATS - Sign in with Microsoft` | Entra ID (turnb.com), single tenant | The login page's Microsoft button; redirect URIs `/azure/callback/` on both hostnames; secret expires 2028-10 |

Uploaded files (job descriptions, bulk-upload CVs, attachments) live in the web
app's own persistent storage at `/home/media` (`MEDIA_ROOT`), not in the image.

Running cost is roughly ₹2,700/month: App Service ~₹1,240, SQL ~₹530, registry
~₹480, OpenAI and Logic Apps by usage. Ask before creating anything else billed.

## Deploying

[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) runs on every push to
`main` (or manually from the Actions tab): it builds the `Dockerfile`, pushes
`turnbhrmsacr2.azurecr.io/ats:<sha>`, and points `turnb-ats` at it. The container
runs `manage.py migrate` on start, so migrations need no separate step.

It needs three repo secrets, all for the resources above:

| Secret | Value |
|---|---|
| `ACR_USERNAME` / `ACR_PASSWORD` | `az acr credential show -n turnbhrmsacr2` |
| `AZURE_WEBAPP_PUBLISH_PROFILE` | `az webapp deployment list-publishing-profiles -g HRMS -n turnb-ats --xml` |

The publish profile only works while **Basic authentication** is on (web app →
Configuration → General settings). If a deploy fails with "No credentials found",
check that first.

Manual fallback, without GitHub (needs only the Azure CLI, no Docker):

```bash
az acr build -r turnbhrmsacr2 -t ats:manual .
az webapp config container set -g HRMS -n turnb-ats \
  --container-image-name turnbhrmsacr2.azurecr.io/ats:manual
```

## App settings

Every setting is an App Service setting (web app → Settings → Environment
variables) - never in git. The full list with comments is in
[`.env.example`](../.env.example); the ones that point at other resources:

| Setting(s) | Points at |
|---|---|
| `DB_HOST`, `DB_USER`, `DB_PASSWORD` (`DB_ENGINE=azure`, `DB_NAME=hr_recruitment`) | `hr-turnb-sql02` |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY` | `turnb-hrms-openai` |
| `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_ACCOUNT_KEY` | `turnbhrmscvs` |
| `LOGIC_APP_CV_PARSER_URL`, `LOGIC_APP_EMAIL_SENDER_URL`, `LOGIC_APP_CALENDAR_URL` | trigger URLs of `cv-parse-single`, `Send-Email-Notifier`, `Interview-Calendar` (they contain a SAS signature) |
| `CV_EXTRACT_API_KEY` | shared with `CV-Automation-Flow-Final`'s `djangoApiKey` parameter - change both together |
| `AZURE_AD_TENANT_ID`, `AZURE_AD_CLIENT_ID`, `AZURE_AD_CLIENT_SECRET` | the app registration above |
| `DOCKER_REGISTRY_SERVER_*` | `turnbhrmsacr2` |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | `turnb-ats.azurewebsites.net` and `ats.turnb.com` |

A Logic App's trigger URL comes from Portal → the workflow → its HTTP trigger,
or `az rest --method post --url "<workflow id>/triggers/<trigger name>/listCallbackUrl?api-version=2019-05-01"`.

## Local development

`start_server.bat` uses the git-ignored `.env`, which points at the **same live
database and resources** - anything you change locally is real data. It
deliberately leaves out `LOGIC_APP_EMAIL_SENDER_URL` and `LOGIC_APP_CALENDAR_URL`,
so a local run never emails candidates or puts meetings on calendars (emails
print to the console instead).

Tests never touch Azure: `DB_ENGINE=sqlite python manage.py test`.

The SQL server only accepts listed IPs. On a new network:

```bash
az sql server firewall-rule create -g HRMS -s hr-turnb-sql02 -n "<who-where>" \
  --start-ip-address <your IP> --end-ip-address <your IP>
```

## Diagnostics

- Portal → `turnb-ats` → **Log stream** for live container output, or
  `az webapp log download -g HRMS -n turnb-ats` for the startup log (look for
  `Running migrations` and `Listening at`).
- **Kudu** (`https://turnb-ats.scm.azurewebsites.net`) for `/home/media` files.
- Logic App runs: Portal → the workflow → **Run history**.

## Custom domain `ats.turnb.com` (not done yet)

Free: turnb.com is our domain and App Service issues a managed certificate on B1.
The app settings and app registration already accept `ats.turnb.com`; what's left
needs whoever holds the turnb.com **GoDaddy** DNS login:

1. Web app → **Custom domains** → Add `ats.turnb.com`; note the verification ID.
2. In GoDaddy DNS: CNAME `ats` → `turnb-ats.azurewebsites.net`, and TXT `asuid.ats`
   → the verification ID.
3. Validate, then add the binding with an **App Service Managed Certificate**.
