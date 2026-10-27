"""Use the core storage account through its private endpoints.

The core storage account accepts traffic only through its private endpoints and from trusted Azure services.
The cases make sure that the account has an endpoint for each storage service, and that the shared services
that use the account still work: the shared Gitea service resolves its Key Vault references, and the Airlock
notifier Logic App loads its workflows.

The notifier seed case runs before the core change. It installs the notifier and keeps it. The upgrade case
runs after the core change. It upgrades this notifier to the current template version. The install case then
installs a new notifier.
"""

import asyncio
import logging
import os

import pytest

from e2e_tests import config
from e2e_tests.helpers import get_admin_token, get_shared_service_by_name
from e2e_tests.resources import strings
from e2e_tests.resources.gitea_secrets import WEB_API
from e2e_tests.resources.resource import get_resource, post_resource
from e2e_tests.resources.sql_probe import arm_client
from e2e_tests.test_shared_services import (
    create_airlock_notifier_properties,
    disable_and_delete_shared_service_if_exists,
    managed_shared_service,
)

LOGGER = logging.getLogger(__name__)
STORAGE_API = "2023-05-01"
NETWORK_API = "2024-05-01"
WORKFLOW_API = "2018-11-01"
SERVICES = ("blob", "file", "queue", "table")
NOTIFIER_WORKFLOW = "AirlockNotifier"
# The shared Gitea web app reads these settings from Key Vault.
GITEA_SECRETS = ("GITEA_PASSWD", "GITEA__database__PASSWD")


def core_group():
    return f"/subscriptions/{os.environ['ARM_SUBSCRIPTION_ID']}/resourceGroups/rg-{config.TRE_ID}"


def core_account_id():
    return f"{core_group()}/providers/Microsoft.Storage/storageAccounts/stg{config.TRE_ID.replace('-', '')}"


def shared_service_payload(template_name, **properties):
    # These names let disable_and_delete_shared_service_if_exists recover a service that a failed run kept.
    return {
        "templateName": template_name,
        "properties": {
            "display_name": f"Shared service {template_name}",
            "description": f"{template_name} deployed via e2e tests",
            **properties,
        },
    }


def notifier_payload():
    return shared_service_payload(strings.AIRLOCK_NOTIFIER_SHARED_SERVICE, **create_airlock_notifier_properties)


def notifier_share_name():
    return f"airlock-notifier-content-{config.TRE_ID}".lower()


def notifier_site_id():
    return f"{core_group()}/providers/Microsoft.Web/sites/airlock-notifier-app-{config.TRE_ID}"


async def notifier_workflows(arm, minutes=20):
    """Wait until the Logic App runtime lists its workflows. The runtime needs its storage for this."""
    path = f"{notifier_site_id()}/hostruntime/runtime/webhooks/workflow/api/management/workflows"
    async with asyncio.timeout(minutes * 60):
        while True:
            try:
                names = sorted(workflow["name"] for workflow in await arm.request("GET", path, WORKFLOW_API))
            except RuntimeError as error:
                LOGGER.info("The notifier runtime is not ready: %s", error)
            else:
                if names:
                    return names
            await asyncio.sleep(30)


async def assert_notifier_works(arm):
    share = notifier_share_name()
    share_id = f"{core_account_id()}/fileServices/default/shares/{share}"
    assert await arm.request("GET", share_id, STORAGE_API, missing_ok=True) is not None, f"No content share {share}"
    workflows = await notifier_workflows(arm)
    assert NOTIFIER_WORKFLOW in workflows, f"The notifier runtime lists {workflows}"
    LOGGER.info("The notifier runtime uses the content share %s and lists %s", share, workflows)


async def key_vault_references(arm, site_id, names, minutes=10):
    """Wait until each Key Vault reference has the status Resolved. Azure returns the status only."""
    async with asyncio.timeout(minutes * 60):
        while True:
            statuses = {}
            for name in names:
                reference = await arm.request("GET", f"{site_id}/config/configreferences/appsettings/{name}", WEB_API)
                statuses[name] = reference["properties"].get("status")
            if all(status == "Resolved" for status in statuses.values()):
                return statuses
            LOGGER.info("Key Vault references are not resolved yet: %s", statuses)
            await asyncio.sleep(30)


async def upgrade_to_current_version(service, admin_token, verify):
    """Upgrade a shared service to the current version of its template."""
    template = await get_resource(
        f"{strings.API_SHARED_SERVICE_TEMPLATES}/{service['templateName']}", admin_token, verify
    )
    path = f"{strings.API_SHARED_SERVICES}/{service['id']}"
    record = (await get_resource(path, admin_token, verify))["sharedService"]
    LOGGER.info("Upgrade %s from %s to %s", service["templateName"], record["templateVersion"], template["version"])
    assert record["templateVersion"] != template["version"], "The seed already has the current version"
    await post_resource(
        {"templateVersion": template["version"]}, path, admin_token, verify, method="PATCH", etag=record["_etag"]
    )
    record = (await get_resource(path, admin_token, verify))["sharedService"]
    assert record["templateVersion"] == template["version"]
    assert record["deploymentStatus"] == strings.RESOURCE_STATUS_UPDATED


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.core_storage_validation
@pytest.mark.timeout(10 * 60)
async def test_core_storage_accepts_only_private_traffic(verify):
    async with arm_client() as arm:
        account = await arm.request("GET", core_account_id(), STORAGE_API)
        rules = account["properties"]["networkAcls"]
        assert rules["defaultAction"] == "Deny"
        assert "AzureServices" in rules["bypass"]
        assert not rules.get("ipRules") and not rules.get("virtualNetworkRules")
        for service in SERVICES:
            endpoint_id = f"{core_group()}/providers/Microsoft.Network/privateEndpoints/pe-{service}-{config.TRE_ID}"
            endpoint = await arm.request("GET", endpoint_id, NETWORK_API)
            connection = endpoint["properties"]["privateLinkServiceConnections"][0]["properties"]
            assert [group.lower() for group in connection["groupIds"]] == [service]
            assert connection["privateLinkServiceId"].lower() == account["id"].lower()
            assert connection["privateLinkServiceConnectionState"]["status"] == "Approved"
    LOGGER.info("The core storage account denies other networks and has approved endpoints for %s", SERVICES)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.core_storage_validation
@pytest.mark.timeout(3 * 60 * 60)
async def test_shared_gitea_uses_the_private_core_storage(verify):
    await disable_and_delete_shared_service_if_exists(strings.GITEA_SHARED_SERVICE, verify)
    # The install creates the Gitea share in the core account through the file endpoint.
    async with managed_shared_service(shared_service_payload(strings.GITEA_SHARED_SERVICE), verify):
        site_id = f"{core_group()}/providers/Microsoft.Web/sites/gitea-{config.TRE_ID}"
        async with arm_client() as arm:
            assert await arm.request("GET", f"{core_account_id()}/fileServices/default/shares/gitea-data", STORAGE_API)
            app = await arm.request("GET", site_id, WEB_API)
            assert app["properties"]["state"] == "Running"
            statuses = await key_vault_references(arm, site_id, GITEA_SECRETS)
        LOGGER.info("The shared Gitea web app runs and resolves %s", sorted(statuses))


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.airlock_notifier_seed
@pytest.mark.timeout(90 * 60)
async def test_seed_airlock_notifier(verify):
    admin_token = await get_admin_token(verify)
    if await get_shared_service_by_name(strings.AIRLOCK_NOTIFIER_SHARED_SERVICE, verify, admin_token):
        raise ValueError("An Airlock notifier already exists")
    await post_resource(
        notifier_payload(), strings.API_SHARED_SERVICES, admin_token, verify, cleanup_failed_create=True
    )
    async with arm_client() as arm:
        workflows = await notifier_workflows(arm)
    assert NOTIFIER_WORKFLOW in workflows, f"The notifier runtime lists {workflows}"
    LOGGER.info("The seed notifier lists %s", workflows)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.airlock_notifier_upgrade
@pytest.mark.timeout(3 * 60 * 60)
async def test_upgrade_airlock_notifier(verify):
    admin_token = await get_admin_token(verify)
    seed = await get_shared_service_by_name(strings.AIRLOCK_NOTIFIER_SHARED_SERVICE, verify, admin_token)
    if seed is None:
        raise ValueError("Run the airlock_notifier_seed case before the core change first")
    try:
        async with arm_client() as arm:
            try:
                workflows = await notifier_workflows(arm, minutes=2)
                LOGGER.info("Before the upgrade, the notifier runtime lists %s", workflows)
            except TimeoutError:
                LOGGER.info("Before the upgrade, the notifier runtime lists no workflows")
            await upgrade_to_current_version(seed, admin_token, verify)
            await assert_notifier_works(arm)
    finally:
        await disable_and_delete_shared_service_if_exists(strings.AIRLOCK_NOTIFIER_SHARED_SERVICE, verify)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.core_storage_validation
@pytest.mark.timeout(3 * 60 * 60)
async def test_install_airlock_notifier(verify):
    await disable_and_delete_shared_service_if_exists(strings.AIRLOCK_NOTIFIER_SHARED_SERVICE, verify)
    async with managed_shared_service(notifier_payload(), verify):
        async with arm_client() as arm:
            await assert_notifier_works(arm)
