# End-to-end (E2E) tests

## Prerequisites

1. Authentication and Authorization configuration set up as noted in the [Authentication documentation](../tre-admins/auth.md)
1. An Azure Tre deployed environment.

## Registering bundles to run End-to-end tests

End-to-end tests depend on certain bundles to be registered within the TRE API.

When End-to-end tests run in CI, they are registered as a prerequisite to running tests.

When running tests locally, use the `prepare-for-e2e` Makefile target:

```cmd
make prepare-for-e2e
```

## Certificate lifecycle tests

Use a TRE without Nexus for tests that remove and reinstall the certificate shared service.
Removing its certificate can interrupt Nexus.
Run only one certificate lifecycle test suite per TRE at a time.
Do not run it alongside Airlock or shared-service tests that use the same certificate service.
Use the same hostname and certificate name when testing reuse after reinstallation.

## Debugging the End-to-End tests

Use the "Run and Debug" panel within Visual Studio Code, select "E2E Extended", "E2E Smoke" or "E2E Performance" in the drop down box and click play.

- This will copy `config.yaml` settings to `/workspaces/AzureTRE/e2e_tests/.env` for you which supplies your authentication details

- This will also use `/workspaces/AzureTRE/core/private.env` file for other values.

If an E2E test fails while waiting for an operation, check its test log.
Use the timestamp and operation ID to find the matching API logs.

If an operation poll gets an unexpected HTTP status, the test logs the UTC time, the operation path, the status and the `request-id` header of the response.
The API puts the trace of each request in this header as `|<trace ID>.<span ID>.`, also for HTTP 500.
App Insights stores the trace ID as `OperationId`. Run this query in the Log Analytics workspace of the environment:

```kusto
union AppRequests, AppExceptions, AppTraces
| where OperationId == "<trace ID>"
| order by TimeGenerated asc
```

## Airlock draft deletion

The draft-deletion case requires AzureCloud, `ARM_SUBSCRIPTION_ID`, `TRE_ID` and the existing CI Azure login.
The CI identity must have ARM read access to blob containers in the TRE Airlock storage account.
Local runs use the Azure CLI login. CI uses its existing federated identity. The test does not add permissions.

After submission, the test checks that the old draft SAS cannot write a new blob.
A `ContainerNotFound` or `AuthorizationPermissionMismatch` response must also have independent ARM evidence:
the draft is absent, and the submitted container exists with matching workspace and import-stage metadata.
A permission error alone cannot pass the test. Other storage errors and ARM lookup failures remain test failures.
These checks share a 60-second deadline. They do not compare submitted file contents. The review-VM lifecycle test also does not verify file contents.

## Validate one bundle

The [bundle coverage map](https://github.com/microsoft/AzureTRE/blob/main/e2e_tests/bundle_coverage.json) lists every Porter bundle, its prerequisite bundles, exact pytest cases and remaining coverage gaps.
Selecting a bundle runs only its declared cases. Fixtures may deploy prerequisite resources.
A passing case does not prove the bundle's full functional, upgrade or custom-action lifecycle.

### Check selection without deploying

Use the E2E Python environment from the repository root:

```bash
python3 e2e_tests/run_bundle.py --bundle tre-workspace-service-azuresql --collect-only
```

This collects `test_azuresql.py::test_sql_query_survives_sku_upgrade` without creating resources.
The case checks a private SQL query and data persistence after an S1-to-S2 SKU change.

The report records `not_run`, even when collection succeeds.
Unknown names, unavailable cases, missing tests and unexpected collected cases fail explicitly.

### Run against a configured environment

After configuring the prerequisites above, run:

```bash
TEST_BUNDLE=tre-workspace-service-azuresql make test-e2e-bundle
```

Use one test process. Per-bundle runs reject a marker expression or multiple workers.
If the selection includes the Guacamole AAD case, clear `TEST_AAD_WORKSPACE_ID`.
That case requires an owned workspace so cascade cleanup removes its new service.
Some shared-service cases replace existing services, so use an isolated validation environment.
Check the selected entry's prerequisites and limitations before running it.

### Export review-VM selection

Select `tre-service-guacamole-export-reviewvm` to run `test_airlock_export_review.py::test_airlock_export_review_vm_flow`.
This case is opt-in. It is not part of `airlock` or `airlock_validation`, so existing Airlock jobs do not acquire another VM lifecycle.
Use the separate bundle job with one process and run other mutating suites sequentially.

The case requires AzureCloud and a configured manual test workspace application.
The test identity must have both `WorkspaceOwner` and `AirlockManager` on that application.
The CI identity needs VM read and managed Run Command permissions for the test resources.
Use an enabled Nexus 3.11.0 or newer, or explicitly accept its EULA so the existing prerequisite helper can create it.
The test does not add role assignments or network access rules.

The case creates a separate workspace, Guacamole service and minimal Windows upload VM.
It ignores configured workspace and service IDs and preserves any reused Nexus and certificate services.
The upload VM sends one synthetic blob to the export draft through the workspace private endpoint.
The SAS is a protected Run Command parameter. The probe does not print it.
The submission fixes the content. Before the submit, the case starts a second probe on the upload VM with a start time.
The case submits the request before that time, so each write of the probe comes after the submission.
For 2 minutes the probe writes changed content through the old draft SAS, and it counts the writes that Azure accepts.
It then tries an overwrite, a new blob and a delete through the same SAS. Each attempt must fail.
After the request reaches `in_review`, the case creates an export review VM in the same workspace.
The review copy must hold only the submitted file with the submitted hash, also if a write after the submission passed.
After the approval, the test runner lists and downloads the approved export through the API link. It must hold only the submitted file with the submitted hash.
The case then waits for automatic VM deletion.
Both VM bundle versions must match the checkout. The evidence report records their observed versions.
Recovery cleanup cannot turn failed automatic deletion into a passing result.

The bundle job sets a 270-minute absolute deadline before container startup.
Provisioning and validation stop with 120 minutes reserved for cleanup, within the 300-minute job limit.
Local runs start that budget when the case begins. Failed setup and probes also trigger bounded cleanup.
Each owned resource receives a share of the remaining cleanup time, so one stalled delete cannot consume the entire reserve.
The case removes its VMs, service and workspace before releasing its Nexus prerequisites.
Interactive desktop access, denied access, rejected exports, upgrades, custom actions and physical resource removal need separate evidence.

### Databricks selection

Select `tre-shared-service-databricks-private-auth` for the authentication service lifecycle.
Select `tre-service-databricks` for its authentication prerequisite, a new base workspace and the private Databricks service.
The branch workflow already publishes and registers both bundles. It completes smoke tests before starting the selected bundle job.
Use one test process and one writer in a dedicated environment.
Do not run these cases alongside smoke fixtures, other bundle tests, workspace deployments or manual resource changes.

Both cases require AzureCloud, matching Azure and authentication tenants, and the existing CI Azure identity.
Preflight requires no TRE workspaces, no authentication service and no records other than SOA in the core Databricks private DNS zone.
It checks core resource ownership and the required network/DNS resources before creating anything.
The workspace-service case also requires the checkout version of the deployed firewall bundle and complete endpoint data for the core region.
The checks use the actual core resource group location. Configured workspace IDs are not reused.

The authentication private endpoint is shared by workspaces in the same region and private DNS zone.
Deleting it can break their browser sign-in. See [Databricks Private Link concepts](https://learn.microsoft.com/en-us/azure/databricks/security/network/concepts/private-link).
The tests create and remove only owned resources. They recheck isolation before authentication removal and retain it if dependent cleanup fails.
These checks do not provide an atomic lock against another writer. Clean up a failed isolated run before trying again.

The tests compare registered and deployed versions with the checkout. ARM checks cover resource identity, ownership tags,
private access, managed identity, injected subnets, private endpoints and their DNS configuration.
Cleanup checks TRE removal and the declared ARM resources. The workspace-service case also checks removal of its firewall rule collections.
The service pipeline upgrades its parent VNet and changes the shared firewall, which is why it requires exclusive use.
The total deadline is 270 minutes, including a 120-minute cleanup reserve. The workflow starts that deadline before container setup.

Browser sign-in, private DNS reachability, cluster and notebook execution, user access and previous-version upgrades remain unproven.
Neither bundle defines custom actions. Endpoint completeness checks do not establish that every regional endpoint is current.
Switzerland North's previously empty storage and Event Hubs lists are corrected using the
[documented regional endpoints](https://learn.microsoft.com/en-us/azure/databricks/resources/ip-domain-region).
The current DBFS storage name contains only the last four characters of the service ID, so a global name collision can still prevent deployment.

### Azure ML selection

Select `tre-service-azureml` for the private parent-service case.
Select `tre-user-resource-aml-compute-instance` for the assigned compute case and its parent prerequisites.
The branch workflow publishes the compute bundle and registers it after Azure ML.
For local execution, build, publish and register the base workspace, Azure ML service and compute bundle first.
Use the separate bundle job with one process and run mutating suites sequentially.

Both cases create an Automatic workspace and require non-empty owner and researcher group IDs.
They ignore configured workspace and service IDs. The old generic Azure ML case is replaced by the focused parent selection.
The test identity needs the existing Automatic-workspace permissions. Automatic workspace group creation must be enabled.
ARM inspection currently supports AzureCloud and requires matching authentication and Azure tenants.

For compute, set the environment variable `TEST_AML_USER_OBJECT_ID` to an existing Entra user's object ID.
In GitHub Actions, set the same name as a secret on the selected GitHub environment.
Do not use an application or service-principal object ID.
Preflight checks UUID syntax. It does not query the directory or establish the user's type, existence or access.
The test does not read Graph, grant roles or add group membership.
The assigned user still needs the normal workspace access before a separate interactive test.

The compute case explicitly requests `Standard_D2_v3`. Check regional capacity and AML quota before execution.
The test checks the deployed bundle versions, exact ARM IDs, TRE ownership tags, private subnet, disabled public IP and assigned user.
It waits for the compute to reach `Running`. It removes compute before the service and workspace, and checks TRE and ARM removal.
The parent case checks successful private AML workspace provisioning and removal.
These ARM checks follow the [compute response contract](https://learn.microsoft.com/en-us/rest/api/azureml/compute/get).

The bundle job sets a 270-minute absolute deadline before container startup, with 120 minutes reserved for cleanup.
Cleanup runs in reverse dependency order and divides the remaining time among owned resources.
Local runs start the same budget when the case begins. Failed provisioning and verification still trigger bounded cleanup.
Missing prerequisites and failed assertions fail the case. They do not count as skipped or passing coverage.

These cases do not prove notebook execution, interactive user access, prior-version upgrades or deletion of every dependent Azure artefact.
The compute bundle defines no custom actions. Retain these limits in each bundle's evidence record.

### OpenAI selection

Select `tre-workspace-service-openai` to run `test_openai.py::test_private_openai_lifecycle`.
The branch workflow registers the OpenAI bundle. The test also replaces its generic case in the `workspace_services` group.
The test requests `gpt-5.1 | 2025-11-13`, private access and one regional `Standard` capacity unit.
It does not switch models or deployment types when Azure cannot satisfy those inputs.

The prerequisite check uses the workspace resource group's actual region and the current subscription model and usage APIs.
It requires a generally available chat model, a current Standard tier and at least one free capacity unit.
It rejects retired model/tier dates, fine-tuning quotas and incomplete or blocked quota data.
The CI identity needs permission to read model and quota data at subscription scope.
The check does not grant roles or reserve capacity. Azure deployment can still fail after it passes.

Prerequisite failures fail the selected test before service creation. They do not count as skipped or passing coverage.
Service creation has a 45-minute deadline. Verification has a separate five-minute deadline.
Failed creation and teardown use the existing helper's separate cleanup deadline.
A configured workspace is retained. A fixture-created workspace uses the fixture's cleanup.
Private inference, access control, upgrades and complete physical cleanup remain separate checks.

### Certificate and Nexus selection

Select `tre-shared-service-certs` to install, uninstall and reinstall the certificate shared service with the same hostname and certificate name.
The case requires certificate bundle version `0.9.0` or later and pins both installations to the selected template version.
It checks the deployed API record, the gateway's public certificate SAN and validity, and the gateway's stopped state.
Reinstallation must preserve the SHA-256 certificate fingerprint and produce a matching resource processor reuse log without issuance evidence.
After each uninstall, the case requires API HTTP 404 and ARM confirmation that the gateway, public IP and challenge storage are absent.
The bundle report records public metadata, reuse and cleanup results. It does not require Nexus EULA consent.

The evidence helper currently supports `AzureCloud`. It uses the existing deployment identity for ARM and Log Analytics access.
If the gateway omits public certificate data, the helper reads the exact referenced certificate version through the resource-processor VM.
This uses a temporary managed Run Command and the VM's existing identity and private network access.
The deployment identity must be able to create, read and delete that command on the resource-processor VM.
The helper removes the command after success, failure or cancellation. It reads public certificate data only, without changing vault access or permissions.
Log evidence is limited to the installation's message spans, so other installations in the same trace cannot affect the result.
Only aggregate log counts enter the test process. Missing, partial or delayed evidence beyond the deadline fails the case.
See [Key Vault public certificate retrieval](https://learn.microsoft.com/en-us/rest/api/keyvault/certificates/get-certificate/get-certificate)
and [Azure Monitor query authentication](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/access-api).

Each installation has a 45-minute provisioning deadline. Cleanup has its own 60-minute deadline.
The complete case has a 285-minute limit, leaving 15 minutes within the five-hour bundle job for setup and reporting.
It refuses to change resources when Nexus is present. Existing certificate resources must belong to earlier E2E tests before recovery can remove them.
Cleanup has a separate deadline and retains the certificate service if Nexus appears before deletion.
Use an isolated environment and run mutating suites sequentially. These guards do not provide a distributed lock.

Select `tre-shared-service-sonatype-nexus` to run the existing certificate/Nexus lifecycle case.
The report identifies certificates and the deployed firewall as prerequisites. Dependency creation does not complete their separate evidence records.
Nexus selection still requires explicit EULA consent. Both selections retain the weekend certificate rate-limit precaution.
After checking rate limits, use `runCertificateTestsOnWeekends=true` in the branch workflow or `TEST_RUN_CERTIFICATE_TESTS_ON_WEEKENDS=true` locally.
A skipped case remains unproven coverage.

The certificate-only case is opt-in through bundle selection or the `certificate_validation` marker.
It is excluded from the ordinary `shared_services` selection to avoid an additional certificate request in that suite.
The existing combined case remains in that suite. It deletes Nexus before its certificate dependency.
Certificate chain validation, actual renewal, retained Key Vault artefacts, Nexus package/proxy use, upgrades and other Azure resource removal need separate evidence.
The case does not purge certificates. A first installation can reuse a certificate retained by an earlier run.

Before merging the certificate fix, run this case from a trusted upstream branch that contains both the certificate implementation and these tests.
Select `e2eBundle=tre-shared-service-certs` in `deploy_tre_branch.yml` and leave the marker selector empty.
PR slash commands use workflow definitions from `main`. `/test` runs smoke tests and `/test-shared-services` excludes this certificate-only case.
A test-only PR without the new certificate bundle cannot validate reuse. Run Nexus and Airlock only after this case and its cleanup pass.

### Base workspace lifecycle

Select `tre-workspace-base` to run `test_workspace_base.py::test_base_workspace_lifecycle` independently.
The case always creates its own workspace with Automatic authentication and backups disabled. It does not reuse `TEST_WORKSPACE_ID`.
It checks the deployed record and uses the configured workspace identity to list the new workspace's services.
It then disables and deletes the workspace, waits for the delete operation and confirms that an API lookup returns HTTP 404.
Body failures still trigger cleanup. If cleanup also fails, the original failure is preserved with an additional diagnostic note.

Setup uses the existing one-hour deployment limit and failed-create recovery. The API checks have a five-minute limit.
Cleanup has a separate one-hour budget. The case is opt-in through bundle selection or the `workspace_validation` marker.
It does not add another workspace to the extended suite.

These checks do not prove network isolation, storage access, upgrades, custom actions or backup restore and retention.
Verify Azure resource removal separately when recording release evidence.

### SQL time limits

The SQL bundle job has a 300-minute limit. Its first step sets an absolute lifecycle deadline at 270 minutes.
Setup and queries stop with 120 minutes remaining for cleanup. Container startup uses the same budget.
The SQL case manages its own workspace and Guacamole service so that setup and cleanup share this deadline.
It preserves pre-created resources and divides the remaining cleanup time between the resources it created.
Failed-create recovery and temporary probe cleanup also count towards the lifecycle deadline.
The final 30 minutes are reserved for results and job overhead. Cleanup failures still fail the test.
For a local run, the 270-minute lifecycle budget starts when the SQL case begins.

### MySQL selection

Select `tre-workspace-service-mysql` to run `test_mysql.py::test_private_mysql_query_survives_sku_change`.
The case creates its own workspace, Guacamole service, Linux VM and MySQL service.
The Linux VM installs the MySQL client through Nexus. Thus the run needs a suitable Nexus instance or `acceptNexusEula=true`.
The case writes a row through private DNS with the Burstable SKU. It then changes the SKU to General Purpose and reads the row again.
The VM reads the administrator password from the workspace Key Vault through a temporary role assignment on that secret only.
The MySQL job uses the SQL time limits above with its own `MYSQL_VALIDATION_DEADLINE`. If the case creates Nexus, it removes Nexus after the VM.
These checks do not prove previous-version upgrades or researcher access.

### Linux VM selection

Select `tre-service-guacamole-linuxvm` to run `test_guacamole_linuxvm.py::test_create_guacamole_linux_vm`.
The case creates a Linux VM and waits for its Nexus-backed bootstrap. Thus the run needs a suitable Nexus instance or `acceptNexusEula=true`.
The case then runs the `stop`, `start` and `reset_password` actions.
After `stop` and `start`, the case reads the VM power state from Azure. After `reset_password`, it makes sure that the Key Vault secret has a new version.
The case does not sign in to the VM, so it does not prove interactive access or the new password.

### Health selection

Select `tre-workspace-service-health` to run `test_health.py::test_private_fhir_access_and_dicom_update`.
The case creates its own workspace, Guacamole service, Windows VM and Health service with FHIR R4.
The Health bundle needs the workspace owners group, and only an Automatic workspace gets Entra ID groups.
So the case always creates an Automatic workspace. It does not use `TEST_WORKSPACE_ID`, `TEST_WORKSPACE_SERVICE_ID` or `TEST_WORKSPACE_APP_ID`.
From the VM, the case makes sure that the FHIR host resolves to a private address and that the FHIR metadata is available.
If the FHIR name lookup fails, the case runs that probe again after 60 seconds, 3 attempts in total. Before this lookup, the probe changes no data.
It also makes sure that FHIR rejects a data request without a token (HTTP 401) and from an identity without a role (HTTP 403).
The case then gives the VM identity a temporary FHIR Data Contributor role on the FHIR service and writes a Patient.
The role can take some minutes to apply, so the probe waits up to 10 minutes for a first HTTP 200.
Until the end of this wait, the probe sends the create again after HTTP 403 or 429.
It sends the search again after HTTP 403, 429, 500, 502, 503 or 504.
If a probe fails, the test error gives the stage and the HTTP status of the failed request.
An update adds DICOM. The case reads the Patient again and makes sure that the DICOM host is private and needs a token.
At the end, the case sends FHIR and DICOM requests without a token from the test runner, outside the workspace network.
Azure must reject each request with HTTP 403. The case tries again for up to 5 minutes, because Azure can take this time to block public traffic.
The case then reads the Health workspace and DICOM again, and makes sure that each has public network access disabled.
The case does not read this value on FHIR. Azure keeps `publicNetworkAccess` `Enabled` on FHIR, also after a request sets `Disabled`.
The Health job uses the SQL time limits with its own `HEALTH_VALIDATION_DEADLINE`.

### Gitea selection

Select `tre-workspace-service-gitea` to run `test_gitea.py::test_private_gitea_repository_survives_update`.
The case creates its own workspace, Guacamole service, Windows VM and Gitea service.
It gives the VM identity a temporary Key Vault Secrets User role on the secret that holds the Gitea admin password.
From the VM, the case makes sure that the Gitea host resolves to a private address and that the Gitea API is available.
It also makes sure that Gitea rejects a repository create without credentials (HTTP 401) and a request with a wrong password (HTTP 401).
The VM then creates a private repository with the admin account and commits a file. An anonymous read of the repository gets HTTP 404.
An update changes the display name, and the case restarts the web app. The VM then reads the file again and deletes the repository.
At the end, the case makes sure that the web app and MySQL have public network access disabled, and that the storage firewall denies by default.
The Gitea job uses the SQL time limits with its own `GITEA_VALIDATION_DEADLINE`.

### Backup restore and delete

Set `e2eTestsCustomSelector` to `backup_retention` or `backup_deletion` to run one case of `test_backup_restore.py::test_file_share_restore_and_workspace_delete`.
Run one case in each workflow run, because each case can take 2 hours. The marker `backup_validation` selects both cases.
Each case creates its own workspace with `enable_backup` on, a Guacamole service and a Windows VM.
The `retain` case sets `delete_backups_on_uninstall` to `false`, and the `delete` case sets it to `true`.
From the VM, the case makes sure that the host of the workspace file share is private and that the VM identity without a role gets HTTP 403.
The case then gives the VM identity a temporary Storage File Data Privileged Contributor role on the workspace resource group. The VM writes a file through the Azure Files REST API.
The role is not on the share, because the Azure Backup delete lock on the storage account also blocks the delete of a role assignment on the share.
An on-demand Azure Backup job makes a recovery point. The VM changes the file, a restore of that file from the recovery point follows, and the VM reads the original content again.
The case then deletes the VM, the Guacamole service and the workspace. Each delete must pass.
After the `delete` case, the workspace resource group must be gone.
After the `retain` case, the vault must keep the stopped item with its recovery point, and the storage account must keep its share and deny public network access.
A second restore from the recovery point must pass.
The last clean-up step turns off soft delete, deletes the item and the vault, and deletes the retained resource group.
This step runs after the workspace delete, also when the case fails earlier. It runs only if the API deleted the workspace, so a failed delete stays for diagnosis.

### Airlock v1 to v2 move

Set `e2eTestsCustomSelector` to `airlock_migration_validation` to run `test_airlock_migration.py::test_legacy_airlock_workspace_moves_to_v2`.
The core must have `enable_legacy_airlock` set to `true`, and the test identity must have `WorkspaceOwner` and `AirlockManager` on the manual test workspace application.
The case follows the steps in [Legacy Airlock Architecture](../azure-tre-overview/airlock-legacy.md#upgrading-to-current-architecture):

1. It creates a Manual workspace with `airlock_version` set to `1`. An import uses the legacy external account and is approved.
1. It calls `POST /migrations` two times. The workspace keeps `airlock_version` `1`.
1. With an import draft in progress, a change to `airlock_version` `2` must get HTTP 400. The case then cancels the draft.
1. The change to `2` must pass. A new import draft must use the consolidated core account and a `-draft` container.
1. The legacy request keeps its record, its `approved` status and `airlock_version` `1`. Its stored files are deleted, as the docs say.
1. A change back to `1` must get HTTP 400.

### Run the branch workflow before merge

1. Push the reviewed changes to a branch in `microsoft/AzureTRE`.
2. Select that branch in **Deploy Azure TRE (branch)**.
3. Set `e2eBundle` to the exact Porter bundle name.
4. Leave `e2eTestsCustomSelector` empty and set `e2eProcesses` to `1`.
5. Keep `skipDeployment` false for a new branch environment.

The workflow validates selection before deployment. It passes the bundle name as data in a JSON request, rather than embedding it in shell code.
Prepared requests require every generated metadata field, a full checkout SHA and non-empty TRE, location and cloud identifiers.
The test runner rejects incomplete requests and mismatches with the current checkout or configured TRE, location and cloud.
Unknown fields are ignored. Workflow metadata may be empty outside GitHub Actions.
Branch and PR-comment references derive different environment IDs. A completed test run does not remove its core environment.

Bundle tests run in a separate job. Marker selections use the existing sequential test groups.
Both paths publish their results to the workflow summary.

Existing slash commands retain their marker-based behaviour. They load workflow definitions from `main`; new branch-workflow inputs do not require merging first.

### Read the evidence

The **E2E Test Results (Bundle)** artefact contains:

- `pytest_e2e_bundle.xml`: test, failure, skip and teardown results.
- `bundle-validation.json`: requested bundle, source version, declared prerequisite versions, requested and collected cases, coverage limitations, commit/workflow provenance, environment identity and observed resource/operation IDs.

The JSON report copies only selected identifiers and operation states. It excludes resource properties, tokens and failure tracebacks.
Source versions describe the checked-out manifests; verify deployed versions separately when reusing an environment.
`passed_selected_cases` means the selected assertions and their teardown passed. The report always leaves `full_lifecycle_proven` false for a separate evidence review.
An entirely skipped selection exits unsuccessfully. Individual skipped cases retain their reasons, and a mixed pass/skip result is marked `partial`.

Unavailable bundles fail with the gap recorded in the map. Adding a selector does not implement missing tests or fix bundle publication and registration gaps.

### CycleCloud server validation

Select `tre-shared-service-cyclecloud` with the branch workflow's bundle input, or use:

```bash
TEST_BUNDLE=tre-shared-service-cyclecloud make test-e2e-bundle
```

Use a dedicated AzureCloud environment with one writer and no existing CycleCloud service or DNS zone.
Publish and register the checkout firewall and CycleCloud bundles. Deploy the firewall before validation.
Register the repaired CycleCloud bundle, version 0.7.11 or later, with `start` and `stop` actions.
Accept the CycleCloud server image terms separately before the run. The test reads the agreement and regional image list. It does not accept terms.
Check `Standard_D4s_v5` capacity and quota in the core region before deployment. Image availability does not prove VM capacity.

The case creates one server and checks its private VM address, DNS configuration, blob private endpoint, managed identity and firewall rules.
It invokes stop/start through TRE and verifies the VM power states. It changes the overview to exercise a same-version Terraform upgrade and checks that the VM identity remains stable.
The test records the existing firewall as reused. Smoke completes before the bundle job starts.

The job sets `CYCLECLOUD_VALIDATION_DEADLINE` before container setup. The test reserves 120 minutes for cleanup within its 270-minute deadline.
It owns an accepted service before polling. Cleanup waits for terminal operations, verifies ownership and then uses TRE deletion.
It verifies removal of the VM, disk, NIC, storage, blob private endpoint, DNS zone and captured identity's roles. It also checks that the original firewall rules are restored.
Failed cleanup fails the test. Core Key Vault credentials can remain soft-deleted. The test does not retrieve or purge them.

This case does not configure the CycleCloud application or create an HPC cluster. Browser access, application TLS trust, private DNS reachability and prior-release upgrades remain unproven.
The existing bundle grants subscription Contributor to its VM and permits SharedSubnet access to ARM and GitHub. It does not disable the storage public endpoint.
A private endpoint and a successful server lifecycle do not prove workspace isolation or storage access restrictions.

### Private OHDSI validation

Select the bundle from a branch that contains its repaired bootstrap and validation case:

```bash
TEST_BUNDLE=tre-workspace-service-ohdsi make test-e2e-bundle
```

Use a dedicated AzureCloud environment with one writer and matching deployment and authentication tenants.
Publish and register the checkout base-workspace, firewall and OHDSI bundles. Deploy the firewall before the test.
Check PostgreSQL 14 capacity for `B_Standard_B1ms`, 32 GiB storage and availability zone 1 in the selected region.
The case creates a fresh Automatic workspace. It disables backups and sets `configure_data_source=false` without data-source properties.

The case checks both private web apps, their pinned images, PostgreSQL networking and the WebAPI identity's vault role.
Successful installation includes the bundle's private WebAPI readiness check and local administrator setup.
A metadata update exercises a same-version upgrade. The test checks the callback and firewall collections after each operation.
It waits for terminal operations before updates or deletion. It owns accepted resources before evidence writes or client closure.

The job sets `OHDSI_VALIDATION_DEADLINE` before container setup. The case reserves 120 minutes for cleanup within 270 minutes.
Cleanup removes the service before its workspace. It checks Azure resource removal, app DNS records, callback removal and firewall restoration.
If service cleanup fails, the case retains the workspace and fails. The shared core PostgreSQL DNS link remains in place.
Key Vault items can remain soft-deleted under the existing retention policy.

Browser sign-in, independent private HTTP and DNS probes, database data persistence and previous-version upgrades remain unproven.
Synapse provisioning, CDM queries and clinical workflows need separate cases. Retained non-empty data-source properties are outside this case.
The Synapse script does not currently inspect `configure_data_source`; empty properties avoid that path.
