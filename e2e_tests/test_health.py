"""Exercise private FHIR access, access control and a DICOM update."""

import asyncio
from functools import partial
import logging
import os
import time
from uuid import uuid4

from httpx import AsyncClient
import pytest

from e2e_tests import config
from e2e_tests.conftest import get_workspace_owner_token
from e2e_tests.resources import strings
from e2e_tests.resources.health_probe import (
    FHIR_DATA_CONTRIBUTOR,
    PUBLIC_BLOCKED,
    public_statuses,
    role_access,
    run_probe,
)
from e2e_tests.helpers import get_auth_header, get_full_endpoint
from e2e_tests.resources.deployment import check_deployment, install_done
from e2e_tests.resources.resource import disable_and_delete_resource, get_resource, post_resource, wait_for
from e2e_tests.resources.sql_lifecycle import sql_lifecycle
from e2e_tests.resources.sql_probe import arm_client, COMPUTE_API, validate_vm

LOGGER = logging.getLogger(__name__)
HEALTH_API = "2024-03-31"
NETWORK_API = "2024-05-01"
HEALTH_PAYLOAD = {
    "templateName": strings.HEALTH_SERVICE,
    "properties": {
        "display_name": "Health lifecycle test",
        "description": "Private FHIR and DICOM validation",
        "deploy_fhir": True,
        "fhir_kind": "R4",
        "deploy_dicom": False,
    },
}
VM_PAYLOAD = {
    "templateName": strings.GUACAMOLE_WINDOWS_USER_RESOURCE,
    "properties": {
        "display_name": "Health test client",
        "description": "Temporary private FHIR validation client",
        "os_image": "Windows 11",
        "vm_size": "2 CPU | 8GB RAM",
        "admin_username": "researcher",
        "shared_storage_access": False,
        **{
            f"install_{tool}": False
            for tool in ("azure_cli", "vscode", "storage_explorer", "git", "python_tools", "r_tools")
        },
    },
}


async def zone_records(arm, endpoint_id):
    # Validation only: the zone group lists the record sets that Azure wrote for each zone.
    group = await arm.request("GET", f"{endpoint_id}/privateDnsZoneGroups/private-dns-zone-group", NETWORK_API)
    return {
        config["name"]: sorted(record.get("fqdn", "") for record in config["properties"].get("recordSets", []))
        for config in group["properties"]["privateDnsZoneConfigs"]
    }


async def install_without_services(workspace_path, token, verify):
    # Validation only: the bundle stops an install without FHIR and DICOM before it changes a resource.
    payload = {
        "templateName": strings.HEALTH_SERVICE,
        "properties": {
            "display_name": "Health without services",
            "description": "Validation only",
            "deploy_fhir": False,
            "deploy_dicom": False,
        },
    }
    async with AsyncClient(verify=verify, timeout=30.0) as client:
        response = await client.post(
            get_full_endpoint(f"/api{workspace_path}/workspace-services"), headers=get_auth_header(token), json=payload
        )
        assert response.status_code == 202, response.text
        path = response.json()["operation"]["resourcePath"]
        operation = response.headers["Location"]
        state = await wait_for(install_done, client, operation, token, [])
        _, message, _ = await check_deployment(client, operation, token)
    words = " ".join(message.replace("\u2502", " ").split())
    LOGGER.info("Validation only: an install without services ended in %s", state)
    assert state == strings.RESOURCE_STATUS_DEPLOYMENT_FAILED
    assert "Deploy FHIR, DICOM or both" in words, words[:500]
    await disable_and_delete_resource(f"/api{path}", token, verify, allow_failed_disable=True)
    LOGGER.info("Validation only: the API deleted the service without FHIR and DICOM")


async def update_health(health_path, token, verify, properties):
    health = (await get_resource(f"/api{health_path}", token, verify))["workspaceService"]
    start = time.monotonic()
    async with asyncio.timeout(60 * 60):
        await post_resource(
            {"properties": properties}, f"/api{health_path}", token, verify, method="PATCH", etag=health["_etag"]
        )
    LOGGER.info("Validation only: the update %s took %d seconds", properties, time.monotonic() - start)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.health_validation
@pytest.mark.timeout(285 * 60)
async def test_private_fhir_access_and_dicom_update(verify):
    if os.environ.get("AZURE_ENVIRONMENT", "AzureCloud") != "AzureCloud":
        raise ValueError("The health probe currently supports AzureCloud only")
    async with sql_lifecycle(verify, deadline_variable="HEALTH_VALIDATION_DEADLINE") as resources:
        # The bundle refuses to install without the workspace owners group.
        workspace_path, workspace_id, guacamole_path, guacamole_id = await resources.setup(groups=True)
        # The owner token gets a current token for each request, so one token lasts the case and its clean-up.
        token = await get_workspace_owner_token(workspace_id, verify)
        workspace = (await get_resource(f"/api{workspace_path}", token, verify))["workspace"]
        assert workspace["properties"].get("workspace_owners_group_id"), "The workspace has no owners group"
        await install_without_services(workspace_path, token, verify)
        health_path, health_id = await resources.create(
            HEALTH_PAYLOAD, f"/api{workspace_path}/workspace-services", token
        )
        vm_path, vm_id = await resources.create(VM_PAYLOAD, f"/api{guacamole_path}/user-resources", token)
        health = (await get_resource(f"/api{health_path}", token, verify))["workspaceService"]
        vm_properties = (await get_resource(f"/api{vm_path}", token, verify))["userResource"]["properties"]
        subscription_id = vm_properties.get("workspace_subscription_id") or os.environ["ARM_SUBSCRIPTION_ID"]
        suffix = f"{workspace_id[-4:]}svc{health_id[-4:]}"
        fhir_url = f"https://hs{suffix}-fhir{suffix}.fhir.azurehealthcareapis.com"
        dicom_url = f"https://hs{suffix}-dicom{suffix}.dicom.azurehealthcareapis.com"
        assert health["properties"]["fhir_url"] == fhir_url
        assert not health["properties"].get("dicom_url")
        azure_vm_id = vm_properties["azure_resource_id"]
        async with arm_client() as arm:
            vm = await arm.request("GET", azure_vm_id, COMPUTE_API)
            group = validate_vm(vm, azure_vm_id, subscription_id, config.TRE_ID, workspace_id, guacamole_id, vm_id)
            workspace_arm_id = f"{group}/providers/Microsoft.HealthcareApis/workspaces/hs{suffix}"
            fhir_id = f"{workspace_arm_id}/fhirservices/fhir{suffix}"
            fhir = await arm.request("GET", fhir_id, HEALTH_API)
            assert fhir["properties"]["provisioningState"] == "Succeeded"
            endpoint_id = f"{group}/providers/Microsoft.Network/privateEndpoints/pe-hs{suffix}"
            endpoint = await arm.request("GET", endpoint_id, NETWORK_API)
            probe_id = uuid4().hex
            probe = partial(run_probe, arm, azure_vm_id, vm["location"], fhir_url=fhir_url, probe_id=probe_id)
            await probe(phase="access")
            LOGGER.info("FHIR metadata is private, and data requests need a token and a role")
            async with role_access(arm, fhir_id, FHIR_DATA_CONTRIBUTOR, vm["identity"]["principalId"], subscription_id):
                await probe(phase="write")
                health = (await get_resource(f"/api{health_path}", token, verify))["workspaceService"]
                async with asyncio.timeout(60 * 60):
                    await post_resource(
                        {"properties": {"deploy_dicom": True}},
                        f"/api{health_path}",
                        token,
                        verify,
                        method="PATCH",
                        etag=health["_etag"],
                    )
                health = (await get_resource(f"/api{health_path}", token, verify))["workspaceService"]
                assert health["properties"]["dicom_url"] == dicom_url
                dicom_id = f"{workspace_arm_id}/dicomservices/dicom{suffix}"
                dicom = await arm.request("GET", dicom_id, HEALTH_API)
                assert dicom["properties"]["provisioningState"] == "Succeeded"
                # A new endpoint stops private access to FHIR for some minutes.
                updated_endpoint = await arm.request("GET", endpoint_id, NETWORK_API)
                assert updated_endpoint["properties"]["resourceGuid"] == endpoint["properties"]["resourceGuid"], (
                    "The update replaced the Health private endpoint"
                )
                await probe(phase="read", dicom_url=dicom_url)
                LOGGER.info("FHIR data survived the update that added DICOM")
            # Check the network settings last, so that a failure here keeps the functional evidence above.
            async with AsyncClient(timeout=30) as client:
                public = await public_statuses(
                    client, {"fhir": f"{fhir_url}/metadata", "dicom": f"{dicom_url}/health/check"}
                )
            # Read the resources again after the update.
            # FHIR is not in this list: Azure keeps publicNetworkAccess Enabled on FHIR, also after a request
            # sets Disabled. The private endpoint on the workspace blocks public traffic to FHIR (checked above).
            network = {}
            for name, resource_id in (("workspace", workspace_arm_id), ("dicom", dicom_id)):
                record = await arm.request("GET", resource_id, HEALTH_API)
                network[name] = record["properties"].get("publicNetworkAccess")
            LOGGER.info("Health public responses: %s, public network access: %s", public, network)
            assert public == dict.fromkeys(public, PUBLIC_BLOCKED), f"Health services answer public requests: {public}"
            assert network == dict.fromkeys(network, "Disabled"), f"Health services allow public access: {network}"

            # Validation only: an update that removes FHIR and an update that adds FHIR to a service with DICOM.
            hosts = {
                "workspace": f"hs{suffix}-workspace.",
                "fhir": f"hs{suffix}-fhir{suffix}.",
                "dicom": f"hs{suffix}-dicom{suffix}.",
            }

            def present(records):
                fqdns = [fqdn for zone in records.values() for fqdn in zone]
                return {name: any(fqdn.startswith(prefix) for fqdn in fqdns) for name, prefix in hosts.items()}

            LOGGER.info(
                "Validation only: zone records before the FHIR changes: %s", await zone_records(arm, endpoint_id)
            )
            await update_health(health_path, token, verify, {"deploy_fhir": False})
            records = await zone_records(arm, endpoint_id)
            LOGGER.info("Validation only: zone records after the FHIR removal: %s", records)
            # A record of the deleted FHIR service has no effect, so only the other records must exist.
            assert present(records)["workspace"] and present(records)["dicom"], records
            endpoint_now = await arm.request("GET", endpoint_id, NETWORK_API)
            assert endpoint_now["properties"]["resourceGuid"] == endpoint["properties"]["resourceGuid"]

            await update_health(health_path, token, verify, {"deploy_fhir": True})
            records = await zone_records(arm, endpoint_id)
            LOGGER.info("Validation only: zone records after the FHIR add: %s", records)
            assert present(records) == {"workspace": True, "fhir": True, "dicom": True}, records
            endpoint_now = await arm.request("GET", endpoint_id, NETWORK_API)
            assert endpoint_now["properties"]["resourceGuid"] == endpoint["properties"]["resourceGuid"]
            fhir = await arm.request("GET", fhir_id, HEALTH_API)
            assert fhir["properties"]["provisioningState"] == "Succeeded"
            new_probe = partial(run_probe, arm, azure_vm_id, vm["location"], fhir_url=fhir_url, probe_id=uuid4().hex)
            await new_probe(phase="access")
            async with role_access(arm, fhir_id, FHIR_DATA_CONTRIBUTOR, vm["identity"]["principalId"], subscription_id):
                await new_probe(phase="write", dicom_url=dicom_url)
            LOGGER.info("Validation only: FHIR and DICOM are private after FHIR was removed and added again")
