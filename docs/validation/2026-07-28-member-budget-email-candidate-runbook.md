# Entra member budget and ACS Email candidate runbook

> Operator-run only. This document does not authorize an agent to create Azure
> resources, run SQL, deploy revisions, start jobs, send email, or change
> production traffic. Keep every live item `PENDING — NOT ACCEPTED` until the
> named human approver records the result.

## 0. Release boundary and exact approved inputs

This release provides:

- administrator-only Entra member budget and estimated-cost views;
- pricing coverage that preserves unpriced requests;
- one active Owner/Admin recipient per tenant;
- ACS Email test mail and deduplicated threshold reminders;
- a 15-minute compensating evaluation job.

It does not provide billing reconciliation, user blocking, automatic model
changes, APIM policy changes, or arbitrary recipients. Keep
`DF_FINOPS_ACTIONS_ENABLED=0`.

Before running any command, a human approver must fill and approve every value
below. Do not infer them from an existing subscription. Do not put an email
address, subscription ID, principal ID, token, secret, connection string, or
resource ID in Git evidence.

```powershell
$ApprovedSubscription = '<approved-subscription-name-or-id>'
$ApprovedResourceGroup = '<approved-resource-group>'
$ApprovedAzureRegion = '<approved-container-app-region>'
$ApprovedAcsDataLocation = '<approved-acs-data-location>'
$ApprovedEmailService = '<approved-email-service-name>'
$ApprovedCommunicationService = '<approved-communication-service-name>'
$ApprovedBackendApp = '<approved-backend-container-app>'
$ApprovedWebApp = '<approved-web-container-app>'
$ApprovedRegistry = '<approved-acr-name>'
$ApprovedJob = '<approved-member-budget-job-name>'
$ApprovedSenderAddress = '<approved-AzureManagedDomain-sender-address>'
$ApprovedAdminRecipient = '<approved-active-owner-or-admin-email>'
$ApprovedAcsEmailRoleDefinitionId = '<approved-minimum-ACS-email-send-role-definition-id>'
$ApprovedSqlServer = '<approved-sql-server-name>'
$ApprovedSqlDatabase = '<approved-sql-database-name>'
$ApprovedPortalUrl = '<approved-https-member-budget-page-url>'
$ReleaseCommit = '<full-40-character-approved-git-commit>'
$PreviousBackendRevision = '<record-before-candidate>'
$PreviousWebRevision = '<record-before-candidate>'
```

Fail closed if any placeholder remains, the resource group/region/data
residency is not explicitly approved, or the sender and administrator recipient
are not explicitly approved.

```powershell
$variables = Get-Variable Approved*,ReleaseCommit,Previous* |
  ForEach-Object { [string]$_.Value }
if ($variables | Where-Object { -not $_ -or $_ -match '^<.*>$' }) {
  throw 'STOP: approved candidate inputs are incomplete'
}
az account set --subscription $ApprovedSubscription
git rev-parse HEAD
git status --short
```

The commit output must exactly equal `$ReleaseCommit`, and the release worktree
must contain no unreviewed tracked changes.

## 1. Create ACS Email resources with managed identity only

Use an authorized operator identity. Never create or retrieve an ACS connection
string or service key.

```powershell
az communication email create `
  --name $ApprovedEmailService `
  --resource-group $ApprovedResourceGroup `
  --location global `
  --data-location $ApprovedAcsDataLocation

az communication email domain create `
  --domain-name AzureManagedDomain `
  --email-service-name $ApprovedEmailService `
  --resource-group $ApprovedResourceGroup `
  --location global `
  --domain-management AzureManaged

az communication create `
  --name $ApprovedCommunicationService `
  --resource-group $ApprovedResourceGroup `
  --location global `
  --data-location $ApprovedAcsDataLocation
```

Resolve the two resource IDs only in the operator shell. Do not copy them into
the candidate record.

```powershell
$EmailDomainId = az communication email domain show `
  --domain-name AzureManagedDomain `
  --email-service-name $ApprovedEmailService `
  --resource-group $ApprovedResourceGroup `
  --query id -o tsv
$CommunicationServiceId = az communication show `
  --name $ApprovedCommunicationService `
  --resource-group $ApprovedResourceGroup `
  --query id -o tsv
if (-not $EmailDomainId -or -not $CommunicationServiceId) {
  throw 'STOP: ACS resource IDs were not resolved'
}
```

Link the Azure Managed Domain to the Communication Services resource. Confirm
the API version in the approved tenant before execution; do not proceed if the
provider rejects it.

```powershell
$CommunicationApiVersion = '2023-04-01'
$linkBody = @{ properties = @{ linkedDomains = @($EmailDomainId) } } |
  ConvertTo-Json -Depth 5 -Compress
az rest `
  --method patch `
  --uri "$CommunicationServiceId?api-version=$CommunicationApiVersion" `
  --body $linkBody `
  --headers 'Content-Type=application/json'
```

Enable the existing backend system-assigned identity and grant only the
separately approved ACS Email data-plane role. Do not grant Owner, Contributor,
or subscription-wide scope.

```powershell
az containerapp identity assign `
  --name $ApprovedBackendApp `
  --resource-group $ApprovedResourceGroup `
  --system-assigned
$BackendPrincipalId = az containerapp identity show `
  --name $ApprovedBackendApp `
  --resource-group $ApprovedResourceGroup `
  --query principalId -o tsv
if (-not $BackendPrincipalId) {
  throw 'STOP: backend managed identity is unavailable'
}
az role assignment create `
  --assignee-object-id $BackendPrincipalId `
  --assignee-principal-type ServicePrincipal `
  --role $ApprovedAcsEmailRoleDefinitionId `
  --scope $CommunicationServiceId
```

Record only resource names, the role-assignment outcome category, and UTC
timestamps. Do not record IDs.

## 2. Apply the additive Azure SQL migration

Use the approved deployment identity, not the runtime managed identity. If a
temporary single-IP firewall rule is required, create it immediately before the
migration and delete it immediately afterward. Never grant DDL to the runtime
identity.

```powershell
$env:DF_FINOPS_SQL_SERVER = $ApprovedSqlServer
$env:DF_FINOPS_SQL_DATABASE = $ApprovedSqlDatabase
python -m backend.finops.migrate
python -m backend.finops.migrate
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: additive migration did not complete twice'
}
```

Verify the following objects through the controlled deployment connection:

- `df_finops.member_budget`;
- `df_finops.notification_setting`;
- `df_finops.budget_alert`;
- the unique alert threshold constraint;
- the request actor/window index.

Retain tables on rollback. Do not drop FinOps evidence.

## 3. Build immutable images

Build from the exact approved commit and record both digests.

```powershell
$ShortSha = git rev-parse --short=12 $ReleaseCommit
az acr build `
  --registry $ApprovedRegistry `
  --image "dataforge-backend:member-budget-$ShortSha" `
  --file backend/Dockerfile .
az acr build `
  --registry $ApprovedRegistry `
  --image "dataforge-web:member-budget-$ShortSha" `
  --file web/Dockerfile .
```

Resolve immutable digests through the approved registry workflow. Do not deploy
floating tags.

```powershell
$BackendImage = "<registry>/dataforge-backend@sha256:<approved-digest>"
$WebImage = "<registry>/dataforge-web@sha256:<approved-digest>"
$BackendSuffix = "mb-$ShortSha"
$WebSuffix = "mb-$ShortSha"
```

## 4. Create backend and web zero-traffic candidates

The backend candidate is created first. It uses ACS endpoint and sender
configuration only; no connection string or key is accepted.

```powershell
$AcsEndpoint = az communication show `
  --name $ApprovedCommunicationService `
  --resource-group $ApprovedResourceGroup `
  --query hostName -o tsv
if (-not $AcsEndpoint) {
  throw 'STOP: ACS endpoint unavailable'
}
$AcsEndpoint = "https://$AcsEndpoint"

az containerapp revision copy `
  --name $ApprovedBackendApp `
  --resource-group $ApprovedResourceGroup `
  --revision-suffix $BackendSuffix `
  --image $BackendImage `
  --set-env-vars `
    DF_FINOPS_MEMBER_BUDGETS_ENABLED=1 `
    DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=1 `
    DF_FINOPS_EMAIL_ALERTS_ENABLED=0 `
    DF_FINOPS_ACTIONS_ENABLED=0 `
    DF_ACS_EMAIL_ENDPOINT=$AcsEndpoint `
    DF_ACS_EMAIL_SENDER_ADDRESS=$ApprovedSenderAddress `
    DF_FINOPS_PORTAL_URL=$ApprovedPortalUrl

az containerapp revision copy `
  --name $ApprovedWebApp `
  --resource-group $ApprovedResourceGroup `
  --revision-suffix $WebSuffix `
  --image $WebImage
```

Explicitly set and verify zero traffic before any signed-in validation:

```powershell
az containerapp ingress traffic set `
  --name $ApprovedBackendApp `
  --resource-group $ApprovedResourceGroup `
  --revision-weight "$PreviousBackendRevision=100" "$ApprovedBackendApp--$BackendSuffix=0"
az containerapp ingress traffic set `
  --name $ApprovedWebApp `
  --resource-group $ApprovedResourceGroup `
  --revision-weight "$PreviousWebRevision=100" "$ApprovedWebApp--$WebSuffix=0"

az containerapp revision list `
  --name $ApprovedBackendApp `
  --resource-group $ApprovedResourceGroup `
  --query "[].{name:name,health:properties.healthState,traffic:properties.trafficWeight}" -o table
az containerapp revision list `
  --name $ApprovedWebApp `
  --resource-group $ApprovedResourceGroup `
  --query "[].{name:name,health:properties.healthState,traffic:properties.trafficWeight}" -o table
```

Stop if either candidate is not `Healthy` or has non-zero production traffic.

## 5. Validate health, authorization, and UI with automatic sending off

Run through the revision-specific candidate endpoints or the approved
candidate-only routing path.

| Check | Required result |
| --- | --- |
| `GET /api/health` | `200` |
| Unauthenticated budget API | `401` |
| Active Owner/Admin budget APIs | `200` |
| Active member budget API | `403` |
| Cross-tenant member reference | rejected |
| Save approved active administrator recipient | success |
| Save another member or external recipient | rejected |
| Send one `[Test]` / `[测试]` email | accepted by ACS and received by the approved administrator |
| Reload in same browser | configuration persists |
| Sign in on second device/browser | configuration persists; no secret or credential is returned |

Desktop and mobile acceptance:

- settings entry is visible only to an authorized administrator;
- member, status, workspace, month-to-date estimated spend, USD budget,
  progress, coverage, reminder state, and edit action are legible;
- loading, partial, empty, not-configured, permission-required, unavailable,
  and conflict states remain truthful;
- no raw Entra object ID, original member identifier, ACS message ID, email
  body, connection string, or internal error body appears;
- mobile has no horizontal overflow and retains keyboard/focus behavior.

Expected local screenshot paths:

- `output/playwright/task6-member-budget-entry-desktop.png`
- `output/playwright/task6-member-budget-page-desktop.png`
- `output/playwright/task6-member-budget-page-mobile.png`

These screenshots are local UI evidence only. They do not prove live Azure,
email delivery, authorization, or persistence across devices.

## 6. Manually reconcile one `$190 / $200` scenario

Use only controlled candidate data in the approved tenant.

1. Create a `$200` UTC calendar-month budget with thresholds `80/95/100`.
2. Query the same tenant, authorized workspaces, actor reference, and UTC month
   from reconciled `df_finops.request_event`.
3. Sum only request facts whose estimated cost is priced/estimated/partial.
4. Count all relevant requests, including unpriced/unavailable rows.
5. Confirm the portal amount equals the independently calculated `$190`.
6. Confirm one or more unpriced rows reduce pricing coverage instead of adding
   `$0`.

Record the query method, row counts, calculated amount, coverage, price-card
revision lineage, and UTC window. Do not record raw identity, request payloads,
provider response IDs, or member email.

## 7. Create the 15-minute Container Apps Job last

Create or update the scheduled job only after Sections 1-6 pass. Keep automatic
alerts disabled at creation time.

```powershell
az containerapp job create `
  --name $ApprovedJob `
  --resource-group $ApprovedResourceGroup `
  --environment '<approved-container-app-environment>' `
  --trigger-type Schedule `
  --cron-expression '*/15 * * * *' `
  --replica-timeout 900 `
  --replica-retry-limit 1 `
  --image $BackendImage `
  --command python `
  --args -m backend.finops.member_budget_refresh `
  --env-vars `
    DF_FINOPS_MEMBER_BUDGETS_ENABLED=1 `
    DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=1 `
    DF_FINOPS_EMAIL_ALERTS_ENABLED=0 `
    DF_FINOPS_ACTIONS_ENABLED=0 `
    DF_ACS_EMAIL_ENDPOINT=$AcsEndpoint `
    DF_ACS_EMAIL_SENDER_ADDRESS=$ApprovedSenderAddress `
    DF_FINOPS_PORTAL_URL=$ApprovedPortalUrl
```

Configure the job's system-assigned managed identity, minimum ACS send role,
Azure SQL access, and the same non-secret settings through the approved
infrastructure workflow. Do not copy credentials into job variables.

## 8. Controlled automatic-alert test, then disable again

This section requires a second explicit human approval while the candidate and
job remain isolated from production traffic.

1. Confirm the `$200` budget, `80/95/100` thresholds, `$190` priced spend, and
   incomplete-coverage scenario.
2. Temporarily set `DF_FINOPS_EMAIL_ALERTS_ENABLED=1` on the isolated job only.
3. Start one execution:

   ```powershell
   az containerapp job start `
     --name $ApprovedJob `
     --resource-group $ApprovedResourceGroup
   ```

4. Confirm exactly one 95% email and one durable alert row.
5. Start the job again and confirm no duplicate email or alert.
6. In a separate controlled period/budget, jump directly above 100%; confirm
   only the highest threshold sends and lower crossed thresholds are recorded
   as suppressed.
7. Immediately restore `DF_FINOPS_EMAIL_ALERTS_ENABLED=0` and verify it on the
   job configuration.

Automatic reminders remain `0` after candidate acceptance. Enabling them in
production requires a separate, explicit approval.

## 9. Redacted logs and evidence

Use a bounded UTC window around candidate validation. Record only:

- candidate revision and job names;
- immutable image digests;
- health and HTTP status categories;
- ACS accepted/received timestamps;
- alert ID only if it is an opaque DataForge reference;
- duplicate count;
- safe error category counts;
- role-assignment outcome;
- rollback revision names.

The retained log excerpt must contain no member email, raw identity/object ID,
subscription/principal/resource ID, ACS message ID, endpoint path containing
identity, request/response body, token, credential, connection string, or
internal exception text. Record zero/non-zero counts for:

- authentication/authorization failures outside expected negative tests;
- `audit_persistence_required`;
- SQL persistence failures;
- ACS `permission_required` / `provider_unavailable`;
- duplicate alert delivery;
- traceback/unhandled server error;
- secret-like markers.

## 10. Rollback and production gate

Rollback triggers include failed health, authorization leakage, incorrect
tenant/workspace scoping, incorrect `$190 / $200` reconciliation, unpriced rows
treated as zero, duplicate email, unsafe error/log content, ACS permission
failure, or UI regression.

Rollback order:

1. Set `DF_FINOPS_EMAIL_ALERTS_ENABLED=0` and stop/disable the scheduled job.
2. Route web traffic back to `$PreviousWebRevision`.
3. Route backend traffic back to `$PreviousBackendRevision`.
4. Recheck health, authorization, and critical logs.
5. Retain additive SQL tables and candidate revisions for investigation; do not
   delete evidence or drop tables.

Production promotion requires explicit human approval after every candidate
item is PASS. Promote backend before web, recheck health and critical logs, and
enable the scheduled job last. `DF_FINOPS_ACTIONS_ENABLED` remains `0`.
