"""Move a legacy Airlock workspace to Airlock v2 with the documented procedure."""

import asyncio
import base64
import hashlib
import json
import logging
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from httpx import AsyncClient
import pytest

from e2e_tests import config
from e2e_tests.airlock import strings as airlock_strings
from e2e_tests.airlock.request import get_request, post_request, wait_for_status
from e2e_tests.conftest import (
    clean_up_test_workspace,
    clean_up_test_workspace_service,
    create_or_get_test_workpace_service,
    create_or_get_test_workspace,
    get_workspace_owner_token,
)
from e2e_tests.helpers import get_admin_token, get_auth_header, get_full_endpoint
from e2e_tests.resources import strings
from e2e_tests.resources.airlock_probe import run_probe
from e2e_tests.resources.nexus import nexus_prerequisites
from e2e_tests.resources.resource import get_resource, post_resource
from e2e_tests.resources.sql_lifecycle import sql_lifecycle
from e2e_tests.resources.sql_probe import arm_client
from e2e_tests.token_provider import TokenProvider
from e2e_tests.test_airlock import create_draft_import_request_with_file, submit_airlock_import_request
from e2e_tests.test_airlock_export_review import VM_PAYLOAD, assert_approved_copy, owned_vm

LOGGER = logging.getLogger(__name__)
STORAGE_API = "2023-05-01"
# The storage accounts of the legacy Airlock module in a workspace.
LEGACY_ACCOUNTS = ("stalimapp", "stalexint", "stalexip", "stalexrej", "stalexblocked")


def require_review_roles(token):
    # This is a prerequisite diagnostic. The API validates the token and roles.
    if isinstance(token, TokenProvider):
        token = token.get_token()
    try:
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        roles = claims.get("roles", [])
        if not isinstance(roles, list) or not {"WorkspaceOwner", "AirlockManager"}.issubset(roles):
            raise ValueError
    except (ValueError, IndexError, TypeError, AttributeError):
        raise ValueError(
            "Airlock migration validation requires WorkspaceOwner and AirlockManager on the test workspace app"
        ) from None


def storage_account(container_url):
    return urlsplit(container_url).hostname.split(".", 1)[0]


async def legacy_accounts(workspace_id):
    """Return the default network action of each legacy workspace account, or None if it does not exist."""
    short_id = workspace_id[-4:]
    group = f"/subscriptions/{os.environ['ARM_SUBSCRIPTION_ID']}/resourceGroups/rg-{config.TRE_ID}-ws-{short_id}"
    found = {}
    async with arm_client() as arm:
        for prefix in LEGACY_ACCOUNTS:
            account_id = f"{group}/providers/Microsoft.Storage/storageAccounts/{prefix}ws{short_id}"
            account = await arm.request("GET", account_id, STORAGE_API, missing_ok=True)
            found[prefix] = None if account is None else account["properties"]["networkAcls"]["defaultAction"]
    return found


async def approve_legacy_export(arm, vm_id, location, workspace_path, workspace_id, token, verify):
    """Upload an export from the workspace VM to the legacy internal account, then approve it."""
    result = await post_request(
        {"type": airlock_strings.EXPORT, "businessJustification": "Legacy export before the move to v2"},
        f"/api{workspace_path}/requests",
        token,
        verify,
        201,
    )
    request_id = str(UUID(result["airlockRequest"]["id"]))
    endpoint = f"/api{workspace_path}/requests/{request_id}"
    container_url = (await get_request(endpoint + "/link", token, verify, 200))["containerUrl"]
    # Do not put the URL in the error: it contains a SAS token.
    if storage_account(container_url) != f"stalexintws{workspace_id[-4:]}" or urlsplit(container_url).path != (
        f"/{request_id}"
    ):
        raise ValueError("Unexpected legacy export draft storage or container")
    probe_id = uuid4().hex
    probe = dict(probe_id=probe_id, blob_name=f"export-{probe_id}.txt", content=f"Legacy export {probe_id}\n")
    await run_probe(arm, vm_id, location, phase="upload", container_url=container_url, **probe)
    await post_request(None, endpoint + "/submit", token, verify, 200)
    async with asyncio.timeout(15 * 60):
        await wait_for_status(airlock_strings.IN_REVIEW_STATUS, token, workspace_path, request_id, verify)
    await post_request(
        {"approval": "True", "decisionExplanation": "Legacy export before the move to v2"},
        endpoint + "/review",
        token,
        verify,
        200,
    )
    async with asyncio.timeout(15 * 60):
        await wait_for_status(airlock_strings.APPROVED_STATUS, token, workspace_path, request_id, verify)
    return request_id, probe["blob_name"], hashlib.sha256(probe["content"].encode()).hexdigest()


async def assert_legacy_export(workspace_path, export, token, verify):
    """The approved legacy export is in the core account and holds the submitted file only."""
    request_id, blob_name, expected_hash = export
    endpoint = f"/api{workspace_path}/requests/{request_id}"
    link = (await get_request(endpoint + "/link", token, verify, 200))["containerUrl"]
    assert storage_account(link) == f"stalexapp{config.TRE_ID.replace('-', '')}"
    await assert_approved_copy(endpoint, token, verify, blob_name, expected_hash)


async def patch_status(workspace_path, properties, token, verify):
    """Send a workspace PATCH and return its HTTP status, without waiting for an operation."""
    workspace = (await get_resource(f"/api{workspace_path}", token, verify))["workspace"]
    headers = get_auth_header(token) | {"etag": workspace["_etag"]}
    async with AsyncClient(verify=verify, timeout=60) as client:
        response = await client.patch(
            get_full_endpoint(f"/api{workspace_path}"), headers=headers, json={"properties": properties}
        )
    return response.status_code


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.airlock_migration_validation
@pytest.mark.nexus_required
@pytest.mark.timeout(285 * 60)
async def test_legacy_airlock_workspace_moves_to_v2(verify):
    if os.environ.get("AZURE_ENVIRONMENT", "AzureCloud") != "AzureCloud":
        raise ValueError("Airlock migration validation currently supports AzureCloud only")
    if not config.TEST_WORKSPACE_APP_ID or not config.TEST_WORKSPACE_APP_SECRET:
        raise ValueError("Airlock migration validation requires a configured manual test workspace application")
    tre = config.TRE_ID.replace("-", "")
    async with sql_lifecycle(verify, deadline_variable="AIRLOCK_MIGRATION_VALIDATION_DEADLINE") as resources:
        workspace_path, workspace_id = await create_or_get_test_workspace(
            auth_type="Manual",
            verify=verify,
            client_id=config.TEST_WORKSPACE_APP_ID,
            client_secret=config.TEST_WORKSPACE_APP_SECRET,
            properties={"enable_airlock": True, "airlock_version": 1},
        )
        resources.own(workspace_path, clean_up_test_workspace, "", workspace_path, verify)
        token = await get_workspace_owner_token(workspace_id, verify)
        require_review_roles(token)
        admin_token = await get_admin_token(verify)
        workspace = (await get_resource(f"/api{workspace_path}", token, verify))["workspace"]
        assert workspace["properties"]["airlock_version"] == 1

        # A Windows VM in the workspace writes the legacy export. Its template reads the Nexus address.
        async with asyncio.timeout(60 * 60):
            await resources.enter("Nexus prerequisites", nexus_prerequisites(verify))
        service_path, service_id = await create_or_get_test_workpace_service(workspace_path, token, "", verify)
        resources.own(service_path, clean_up_test_workspace_service, "", service_path, workspace_id, verify)
        vm_path, _ = await resources.create(VM_PAYLOAD, f"/api{service_path}/user-resources", token)

        # Legacy behaviour: an import uses the legacy external account and completes.
        legacy_id, legacy_url = await submit_airlock_import_request(workspace_path, token, verify)
        assert storage_account(legacy_url) == f"stalimex{tre}"
        await post_request(
            {"approval": "True", "decisionExplanation": "Legacy import before the move to v2"},
            f"/api{workspace_path}/requests/{legacy_id}/review",
            token,
            verify,
            200,
        )
        await wait_for_status(airlock_strings.APPROVED_STATUS, token, workspace_path, legacy_id, verify)
        LOGGER.info("A legacy import passed in the v1 workspace")

        # Legacy behaviour: an export from the workspace VM goes through the legacy accounts to the core account.
        async with arm_client() as arm:
            vm_id, location = await owned_vm(
                arm, vm_path, strings.GUACAMOLE_WINDOWS_USER_RESOURCE, workspace_id, service_id, token, verify
            )
            export = await approve_legacy_export(arm, vm_id, location, workspace_path, workspace_id, token, verify)
        await assert_legacy_export(workspace_path, export, token, verify)
        LOGGER.info("A legacy export passed in the v1 workspace")

        # The migration stamps pre-v2 workspaces. A second call must change nothing.
        for attempt in (1, 2):
            result = await post_request(None, "/api/migrations", admin_token, verify, 202)
            LOGGER.info("Migration call %d: %s", attempt, result["migrations"])
            workspace = (await get_resource(f"/api{workspace_path}", token, verify))["workspace"]
            assert workspace["properties"]["airlock_version"] == 1

        # A request in progress blocks the move, so that its data is not left in legacy storage.
        draft_id, draft_url = await create_draft_import_request_with_file(workspace_path, token, verify)
        assert storage_account(draft_url) == f"stalimex{tre}"
        assert await patch_status(workspace_path, {"airlock_version": 2}, admin_token, verify) == 400
        await post_request(None, f"/api{workspace_path}/requests/{draft_id}/cancel", token, verify, 200)

        # The legacy workspace accounts exist and accept only private and trusted service traffic.
        before = await legacy_accounts(workspace_id)
        LOGGER.info("Legacy workspace accounts before the move: %s", before)
        assert before == dict.fromkeys(LEGACY_ACCOUNTS, "Deny")

        workspace = (await get_resource(f"/api{workspace_path}", token, verify))["workspace"]
        async with asyncio.timeout(60 * 60):
            await post_resource(
                {"properties": {"airlock_version": 2}},
                f"/api{workspace_path}",
                admin_token,
                verify,
                method="PATCH",
                etag=workspace["_etag"],
            )
        workspace = (await get_resource(f"/api{workspace_path}", token, verify))["workspace"]
        assert workspace["properties"]["airlock_version"] == 2
        LOGGER.info("The workspace moved to Airlock v2")

        # V2 behaviour: a new request uses the consolidated core account and its own draft container.
        new_id, new_url = await create_draft_import_request_with_file(workspace_path, token, verify)
        assert storage_account(new_url) == f"stalairlock{tre}"
        assert urlsplit(new_url).path == f"/{new_id}-draft"
        new_request = (await get_request(f"/api{workspace_path}/requests/{new_id}", token, verify, 200))[
            "airlockRequest"
        ]
        assert new_request["airlock_version"] == 2
        await post_request(None, f"/api{workspace_path}/requests/{new_id}/cancel", token, verify, 200)

        # The legacy request keeps its record and its version. The move deletes its stored files, as documented.
        legacy = (await get_request(f"/api{workspace_path}/requests/{legacy_id}", token, verify, 200))["airlockRequest"]
        assert legacy["status"] == airlock_strings.APPROVED_STATUS
        assert legacy["airlock_version"] == 1
        exported = (await get_request(f"/api{workspace_path}/requests/{export[0]}", token, verify, 200))[
            "airlockRequest"
        ]
        assert exported["status"] == airlock_strings.APPROVED_STATUS
        assert exported["airlock_version"] == 1
        # The approved export is in the core account, so the move of the workspace keeps it.
        await assert_legacy_export(workspace_path, export, token, verify)
        LOGGER.info("The approved legacy export is still available after the move")
        after = await legacy_accounts(workspace_id)
        LOGGER.info("Legacy workspace accounts after the move: %s", after)
        assert after == dict.fromkeys(LEGACY_ACCOUNTS)

        # A downgrade would remove access to data that v2 keeps.
        assert await patch_status(workspace_path, {"airlock_version": 1}, admin_token, verify) == 400
        LOGGER.info("The API refused the move back to Airlock v1")
