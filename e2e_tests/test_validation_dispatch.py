"""Validation only: an unsent first step after an API worker stop, and an upgrade after an uninstall.

The API and the resource processor of the validation branch have hooks for the display names below. The API
worker stops after it writes an install operation and before it sends the first step. The resource processor
uninstalls the Porter installation before an upgrade. The case logs only states, times and the API messages.
"""

import asyncio
import logging
import time

import httpx
import pytest

from e2e_tests import config
from e2e_tests.helpers import get_admin_token, get_auth_header, get_full_endpoint
from e2e_tests.resources import strings
from e2e_tests.resources.resource import disable_and_delete_resource

LOGGER = logging.getLogger(__name__)
FINAL = {"deployed", "deployment_failed", "updated", "updating_failed"}


def workspace_payload(name, auth_type):
    properties = {
        "display_name": name,
        "description": "Validation only",
        "auth_type": auth_type,
        "address_space_size": "small",
        "enable_backup": False,
    }
    if config.TEST_WORKSPACE_APP_PLAN:
        properties["app_service_plan_sku"] = config.TEST_WORKSPACE_APP_PLAN
    if auth_type == "Manual":
        properties["client_id"] = config.TEST_WORKSPACE_APP_ID
        properties["client_secret"] = config.TEST_WORKSPACE_APP_SECRET
    return {"templateName": strings.BASE_WORKSPACE, "properties": properties}


async def create_and_find(client, payload, token):
    name = payload["properties"]["display_name"]
    try:
        response = await client.post(
            get_full_endpoint(strings.API_WORKSPACES), headers=get_auth_header(token), json=payload, timeout=120
        )
        LOGGER.info("Validation only: the create of %s returned HTTP %s", name, response.status_code)
    except httpx.HTTPError as error:
        LOGGER.info("Validation only: the create of %s got %s", name, type(error).__name__)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        response = await client.get(get_full_endpoint(strings.API_WORKSPACES), headers=get_auth_header(token))
        if response.status_code == 200:
            matches = [w["id"] for w in response.json()["workspaces"] if w["properties"].get("display_name") == name]
            if matches:
                return matches[0]
        await asyncio.sleep(15)
    raise AssertionError(f"The API has no workspace {name} after the create")


async def wait_operation(client, workspace_id, token, action, minutes):
    endpoint = get_full_endpoint(f"{strings.API_WORKSPACES}/{workspace_id}/operations")
    start = time.monotonic()
    while time.monotonic() - start < minutes * 60:
        response = await client.get(endpoint, headers=get_auth_header(token))
        if response.status_code == 200:
            operations = [o for o in response.json()["operations"] if o["action"] == action]
            if operations:
                operation = max(operations, key=lambda o: o.get("createdWhen", 0))
                if operation["status"] in FINAL:
                    message = " ".join(str(operation.get("message", "")).split())
                    LOGGER.info(
                        "Validation only: the %s of %s ended in %s after %d seconds: %s",
                        action,
                        workspace_id,
                        operation["status"],
                        time.monotonic() - start,
                        message[:400],
                    )
                    return operation["status"], message
        await asyncio.sleep(30)
    raise AssertionError(f"The {action} of {workspace_id} did not end in {minutes} minutes")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.dispatch_validation
@pytest.mark.timeout(300 * 60)
async def test_unsent_first_step_and_upgrade_after_uninstall(verify):
    token = await get_admin_token(verify=verify)
    created = []
    try:
        async with httpx.AsyncClient(verify=verify, timeout=60.0) as client:
            # Q1 without a sensitive value: the API sends the first step again.
            first = await create_and_find(
                client, workspace_payload("validation-stop-before-send-a", "Automatic"), token
            )
            created.append(first)
            state, message = await wait_operation(client, first, token, "install", 90)
            assert state == "deployed", message[:500]

            # Q4: the resource processor uninstalls the installation outside the API, so the upgrade must stop.
            headers = get_auth_header(token)
            headers["etag"] = "*"
            response = await client.patch(
                get_full_endpoint(f"{strings.API_WORKSPACES}/{first}"),
                headers=headers,
                json={"properties": {"display_name": "validation-upgrade-after-uninstall"}},
            )
            assert response.status_code == 202, response.status_code
            state, message = await wait_operation(client, first, token, "upgrade", 90)
            assert state == "updating_failed", message[:500]
            assert "An upgrade does not install a deleted resource" in message, message[:500]

            # Q1 with a sensitive value: the API fails the step and asks for the request again.
            second = await create_and_find(client, workspace_payload("validation-stop-before-send-b", "Manual"), token)
            created.append(second)
            state, message = await wait_operation(client, second, token, "install", 30)
            assert state == "deployment_failed", message[:500]
            assert "The API stopped before it recorded the send of this request" in message, message[:500]
    finally:
        for workspace_id in created:
            try:
                await disable_and_delete_resource(
                    f"{strings.API_WORKSPACES}/{workspace_id}", token, verify, allow_failed_disable=True
                )
                LOGGER.info("Validation only: the API deleted workspace %s", workspace_id)
            except Exception as error:
                LOGGER.warning("Validation only: the delete of workspace %s failed: %r", workspace_id, error)
