"""Check the order and the checks of the Airlock v1 to v2 move without Azure."""

import base64
from contextlib import asynccontextmanager
import json
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from e2e_tests import test_airlock_migration as migration
from e2e_tests.resources import sql_lifecycle
from e2e_tests.token_provider import TokenProvider

WORKSPACE = "11111111-1111-4111-8111-111111111111"
PATH = f"/workspaces/{WORKSPACE}"
EXPORT = "22222222-2222-4222-8222-222222222222"


def token(roles):
    # get_workspace_owner_token returns a TokenProvider, not the token text.
    payload = base64.urlsafe_b64encode(json.dumps({"roles": roles}).encode()).decode().rstrip("=")
    credential = Mock()
    credential.get_token.return_value.token = "header." + payload + ".signature"
    return TokenProvider(credential, "api://workspace/.default")


class MigrationFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.version = 1
        self.states = {}
        self.approved_account = "stalexapptretest"
        self.draft_account = "stalexintws1111"
        self.patch_codes = {2: 400, 1: 400}
        self.enterContext(
            patch.dict(os.environ, AZURE_ENVIRONMENT="AzureCloud", AIRLOCK_MIGRATION_VALIDATION_DEADLINE="")
        )
        for name, value in (
            ("TRE_ID", "tre-test"),
            ("TEST_WORKSPACE_APP_ID", "app"),
            ("TEST_WORKSPACE_APP_SECRET", "s"),
        ):
            self.enterContext(patch.object(migration.config, name, value))
        create = AsyncMock(return_value=(PATH, WORKSPACE))
        self.create = self.enterContext(patch.object(migration, "create_or_get_test_workspace", create))
        self.enterContext(
            patch.object(
                migration,
                "clean_up_test_workspace",
                AsyncMock(side_effect=lambda *a: self.events.append("delete-workspace")),
            )
        )
        owner = token(["WorkspaceOwner", "AirlockManager"])
        self.enterContext(patch.object(migration, "get_workspace_owner_token", AsyncMock(return_value=owner)))
        self.enterContext(patch.object(migration, "get_admin_token", AsyncMock(return_value="admin")))

        @asynccontextmanager
        async def nexus(verify):
            self.events.append("nexus")
            yield
            self.events.append("nexus-exit")

        self.enterContext(patch.object(migration, "nexus_prerequisites", nexus))

        async def service(*args):
            self.events.append("guacamole")
            return f"{PATH}/workspace-services/svc", "svc"

        self.enterContext(patch.object(migration, "create_or_get_test_workpace_service", service))
        self.enterContext(
            patch.object(
                migration,
                "clean_up_test_workspace_service",
                AsyncMock(side_effect=lambda *a: self.events.append("delete-guacamole")),
            )
        )

        async def create_vm(payload, endpoint, *args, **kwargs):
            self.events.append("vm")
            return f"{endpoint.removeprefix('/api')}/vm", "vm"

        self.enterContext(patch.object(sql_lifecycle, "post_resource", create_vm))
        self.enterContext(
            patch.object(
                sql_lifecycle.SQLResources,
                "delete",
                AsyncMock(side_effect=lambda *a: self.events.append("delete-vm")),
            )
        )
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=Mock())
        client.__aexit__ = AsyncMock(return_value=False)
        self.enterContext(patch.object(migration, "arm_client", Mock(return_value=client)))
        self.enterContext(patch.object(migration, "owned_vm", AsyncMock(return_value=("vm-id", "switzerlandnorth"))))
        self.enterContext(
            patch.object(migration, "run_probe", AsyncMock(side_effect=lambda *a, **k: self.events.append("upload")))
        )
        self.enterContext(
            patch.object(
                migration, "assert_approved_copy", AsyncMock(side_effect=lambda *a: self.events.append("approved-copy"))
            )
        )

        async def resource(endpoint, *args):
            return {"workspace": {"_etag": "etag", "properties": {"airlock_version": self.version}}}

        self.enterContext(patch.object(migration, "get_resource", resource))

        async def submit(*args):
            self.events.append("legacy-import")
            return "legacy", "https://stalimextretest.blob.core.windows.net/legacy?sig=x"

        async def draft(*args):
            account = "stalimextretest" if self.version == 1 else "stalairlocktretest"
            name = f"draft-v{self.version}"
            self.events.append(name)
            suffix = "-draft" if self.version == 2 else ""
            return name, f"https://{account}.blob.core.windows.net/{name}{suffix}?sig=x"

        self.enterContext(patch.object(migration, "submit_airlock_import_request", submit))
        self.enterContext(patch.object(migration, "create_draft_import_request_with_file", draft))

        async def post(payload, endpoint, *args):
            if endpoint.endswith("/requests"):
                self.events.append(payload["type"])
                return {"airlockRequest": {"id": EXPORT}}
            self.events.append(endpoint.rsplit("/", 1)[-1])
            return {"migrations": []}

        self.enterContext(patch.object(migration, "post_request", post))

        async def wait(state, token, path, request_id, verify):
            self.events.append(state)
            self.states[request_id] = state

        self.enterContext(patch.object(migration, "wait_for_status", wait))

        async def request(endpoint, *args):
            if endpoint.endswith("/link"):
                request_id = endpoint.rsplit("/", 2)[-2]
                account = self.approved_account if self.states.get(request_id) == "approved" else self.draft_account
                return {"containerUrl": f"https://{account}.blob.core.windows.net/{request_id}?sig=x"}
            request_id = endpoint.rsplit("/", 1)[-1]
            version = 2 if request_id == "draft-v2" else 1
            return {"airlockRequest": {"status": "approved", "airlock_version": version}}

        self.enterContext(patch.object(migration, "get_request", request))

        async def patch_status(path, properties, *args):
            self.events.append(f"patch-v{properties['airlock_version']}")
            return self.patch_codes[properties["airlock_version"]]

        self.enterContext(patch.object(migration, "patch_status", patch_status))

        async def move(payload, *args, **kwargs):
            self.events.append("move-to-v2")
            self.version = 2

        self.enterContext(patch.object(migration, "post_resource", move))

        async def accounts(workspace_id):
            self.events.append(f"accounts-v{self.version}")
            return dict.fromkeys(migration.LEGACY_ACCOUNTS, "Deny" if self.version == 1 else None)

        self.enterContext(patch.object(migration, "legacy_accounts", accounts))

    async def test_the_move_follows_the_documented_steps(self):
        await migration.test_legacy_airlock_workspace_moves_to_v2(True)
        self.assertEqual(
            self.events,
            [
                "nexus",
                "guacamole",
                "vm",
                "legacy-import",
                "review",
                "approved",
                "export",
                "upload",
                "submit",
                "in_review",
                "review",
                "approved",
                "approved-copy",
                "migrations",
                "migrations",
                "draft-v1",
                "patch-v2",
                "cancel",
                "accounts-v1",
                "move-to-v2",
                "draft-v2",
                "cancel",
                "approved-copy",
                "accounts-v2",
                "patch-v1",
                "delete-vm",
                "delete-guacamole",
                "nexus-exit",
                "delete-workspace",
            ],
        )
        self.assertEqual(self.create.await_args.kwargs["properties"], {"enable_airlock": True, "airlock_version": 1})
        self.assertEqual(self.create.await_args.kwargs["auth_type"], "Manual")

    async def test_a_move_with_a_request_in_progress_must_be_refused(self):
        self.patch_codes[2] = 202
        with self.assertRaises(AssertionError):
            await migration.test_legacy_airlock_workspace_moves_to_v2(True)
        self.assertNotIn("move-to-v2", self.events)
        self.assertEqual(self.events[-1], "delete-workspace")

    async def test_a_missing_review_role_fails_before_any_request(self):
        self.enterContext(
            patch.object(migration, "get_workspace_owner_token", AsyncMock(return_value=token(["WorkspaceOwner"])))
        )
        with self.assertRaisesRegex(ValueError, "AirlockManager"):
            await migration.test_legacy_airlock_workspace_moves_to_v2(True)
        self.assertEqual(self.events, ["delete-workspace"])

    async def test_an_export_draft_outside_the_legacy_workspace_account_fails_before_the_upload(self):
        self.draft_account = "stalairlockgtretest"
        with self.assertRaisesRegex(ValueError, "Unexpected legacy export draft storage"):
            await migration.test_legacy_airlock_workspace_moves_to_v2(True)
        self.assertNotIn("upload", self.events)
        self.assertEqual(self.events[-4:], ["delete-vm", "delete-guacamole", "nexus-exit", "delete-workspace"])

    async def test_an_approved_export_outside_the_core_account_fails(self):
        self.approved_account = "stalexintws1111"
        with self.assertRaises(AssertionError):
            await migration.test_legacy_airlock_workspace_moves_to_v2(True)
        self.assertNotIn("approved-copy", self.events)
        self.assertNotIn("move-to-v2", self.events)

    def test_the_storage_account_comes_from_the_host(self):
        self.assertEqual(migration.storage_account("https://stalimextre.blob.core.windows.net/x?sig=y"), "stalimextre")

    async def test_legacy_accounts_that_stay_after_the_move_fail_the_case(self):
        async def accounts(workspace_id):
            self.events.append(f"accounts-v{self.version}")
            return dict.fromkeys(migration.LEGACY_ACCOUNTS, "Deny")

        self.enterContext(patch.object(migration, "legacy_accounts", accounts))
        with self.assertRaises(AssertionError):
            await migration.test_legacy_airlock_workspace_moves_to_v2(True)
        self.assertEqual(self.events[-1], "delete-workspace")


class LegacyAccountTests(unittest.IsolatedAsyncioTestCase):
    async def test_accounts_report_their_default_action_or_absence(self):
        arm = Mock()

        async def request(method, resource_id, api_version, missing_ok=False):
            self.assertTrue(missing_ok)
            if "/stalexip" in resource_id:
                return None
            return {"properties": {"networkAcls": {"defaultAction": "Deny"}}}

        arm.request = request
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=arm)
        client.__aexit__ = AsyncMock(return_value=False)
        self.enterContext(patch.dict(os.environ, ARM_SUBSCRIPTION_ID="sub"))
        self.enterContext(patch.object(migration.config, "TRE_ID", "tre-test"))
        self.enterContext(patch.object(migration, "arm_client", Mock(return_value=client)))
        found = await migration.legacy_accounts(WORKSPACE)
        self.assertEqual(found["stalexip"], None)
        self.assertEqual(found["stalimapp"], "Deny")
        self.assertEqual(len(found), 5)
