"""Check the private core storage cases without Azure."""

from contextlib import asynccontextmanager
import os
import unittest
from unittest.mock import AsyncMock, patch

from e2e_tests import test_core_storage_network as storage

GROUP = "/subscriptions/00000000-0000-4000-8000-000000000000/resourceGroups/rg-tre1"
ACCOUNT = f"{GROUP}/providers/Microsoft.Storage/storageAccounts/stgtre1"


class Arm:
    def __init__(self, test):
        self.test = test

    async def request(self, method, resource_id, api_version, missing_ok=False):
        self.test.calls.append((method, resource_id.removeprefix(GROUP)))
        answer = self.test.answers.get(resource_id.removeprefix(GROUP))
        if callable(answer):
            return answer()
        if answer is None and not missing_ok:
            raise RuntimeError(f"ARM GET {resource_id} failed with HTTP 404")
        return answer


def endpoint(service, status="Approved"):
    connection = {
        "groupIds": [service],
        "privateLinkServiceId": ACCOUNT,
        "privateLinkServiceConnectionState": {"status": status},
    }
    return {"properties": {"privateLinkServiceConnections": [{"properties": connection}]}}


class CoreStorageNetworkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []
        self.events = []
        self.enterContext(patch.dict(os.environ, ARM_SUBSCRIPTION_ID=GROUP.split("/")[2]))
        self.enterContext(patch.object(storage.config, "TRE_ID", "tre1"))
        self.enterContext(patch.object(storage.asyncio, "sleep", AsyncMock()))
        rules = {"defaultAction": "Deny", "bypass": "AzureServices", "ipRules": [], "virtualNetworkRules": []}
        self.answers = {
            "/providers/Microsoft.Storage/storageAccounts/stgtre1": {
                "id": ACCOUNT,
                "properties": {"networkAcls": rules},
            },
            "/providers/Microsoft.Storage/storageAccounts/stgtre1/fileServices/default/shares/airlock-notifier-content-tre1": {},
            "/providers/Microsoft.Web/sites/airlock-notifier-app-tre1/hostruntime/runtime/webhooks/workflow/api/management/workflows": [
                {"name": "AirlockNotifier"}
            ],
        }
        for service in storage.SERVICES:
            self.answers[f"/providers/Microsoft.Network/privateEndpoints/pe-{service}-tre1"] = endpoint(service)

        @asynccontextmanager
        async def arm_client():
            yield Arm(self)

        self.enterContext(patch.object(storage, "arm_client", arm_client))
        self.enterContext(patch.object(storage, "get_admin_token", AsyncMock(return_value="admin")))
        self.enterContext(
            patch.object(
                storage,
                "disable_and_delete_shared_service_if_exists",
                AsyncMock(side_effect=lambda name, verify: self.events.append(f"delete {name}")),
            )
        )

    async def test_core_storage_passes_with_four_approved_endpoints(self):
        await storage.test_core_storage_accepts_only_private_traffic(True)

    async def test_core_storage_fails_when_other_networks_can_connect(self):
        self.answers["/providers/Microsoft.Storage/storageAccounts/stgtre1"]["properties"]["networkAcls"][
            "defaultAction"
        ] = "Allow"
        with self.assertRaises(AssertionError):
            await storage.test_core_storage_accepts_only_private_traffic(True)

    async def test_core_storage_fails_without_an_approved_table_endpoint(self):
        self.answers["/providers/Microsoft.Network/privateEndpoints/pe-table-tre1"] = endpoint("table", "Pending")
        with self.assertRaises(AssertionError):
            await storage.test_core_storage_accepts_only_private_traffic(True)

    async def test_notifier_workflows_wait_for_the_runtime(self):
        answers = [RuntimeError("HTTP 503"), [], [{"name": "AirlockNotifier"}]]

        class Runtime:
            async def request(self, method, resource_id, api_version, missing_ok=False):
                answer = answers.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return answer

        self.assertEqual(await storage.notifier_workflows(Runtime()), ["AirlockNotifier"])
        self.assertEqual(storage.asyncio.sleep.await_count, 2)

    async def test_upgrade_needs_the_seed(self):
        self.enterContext(patch.object(storage, "get_shared_service_by_name", AsyncMock(return_value=None)))
        with self.assertRaisesRegex(ValueError, "airlock_notifier_seed"):
            await storage.test_upgrade_airlock_notifier(True)
        self.assertEqual(self.events, [])

    async def test_upgrade_patches_the_current_version_and_deletes_the_notifier(self):
        seed = {"id": "seed", "templateName": storage.strings.AIRLOCK_NOTIFIER_SHARED_SERVICE}
        self.enterContext(patch.object(storage, "get_shared_service_by_name", AsyncMock(return_value=seed)))
        records = iter(
            [
                {"sharedService": {"templateVersion": "1.0.14", "_etag": "etag"}},
                {"sharedService": {"templateVersion": "1.0.15", "deploymentStatus": "updated"}},
            ]
        )

        async def get_resource(endpoint, token, verify):
            if endpoint.startswith(storage.strings.API_SHARED_SERVICE_TEMPLATES):
                return {"version": "1.0.15"}
            return next(records)

        post = AsyncMock(side_effect=lambda *a, **k: self.events.append("patch"))
        self.enterContext(patch.object(storage, "get_resource", get_resource))
        self.enterContext(patch.object(storage, "post_resource", post))
        await storage.test_upgrade_airlock_notifier(True)
        self.assertEqual(post.await_args.args[0], {"templateVersion": "1.0.15"})
        self.assertEqual(post.await_args.kwargs["etag"], "etag")
        self.assertEqual(self.events, ["patch", f"delete {storage.strings.AIRLOCK_NOTIFIER_SHARED_SERVICE}"])

    async def test_a_failed_upgrade_still_deletes_the_notifier(self):
        seed = {"id": "seed", "templateName": storage.strings.AIRLOCK_NOTIFIER_SHARED_SERVICE}
        self.enterContext(patch.object(storage, "get_shared_service_by_name", AsyncMock(return_value=seed)))
        self.enterContext(
            patch.object(storage, "upgrade_to_current_version", AsyncMock(side_effect=AssertionError("failed")))
        )
        with self.assertRaises(AssertionError):
            await storage.test_upgrade_airlock_notifier(True)
        self.assertEqual(self.events, [f"delete {storage.strings.AIRLOCK_NOTIFIER_SHARED_SERVICE}"])

    async def test_gitea_waits_until_each_reference_is_resolved(self):
        site = "/providers/Microsoft.Web/sites/gitea-tre1"
        self.answers[f"{site}"] = {"properties": {"state": "Running"}}
        self.answers["/providers/Microsoft.Storage/storageAccounts/stgtre1/fileServices/default/shares/gitea-data"] = {
            "name": "gitea-data"
        }
        statuses = iter(["Initialized", "Resolved", "Resolved", "Resolved"])
        for name in storage.GITEA_SECRETS:
            self.answers[f"{site}/config/configreferences/appsettings/{name}"] = lambda: {
                "properties": {"status": next(statuses)}
            }

        @asynccontextmanager
        async def managed(payload, verify):
            self.events.append(f"install {payload['templateName']}")
            yield "/shared-services/gitea"

        self.enterContext(patch.object(storage, "managed_shared_service", managed))
        await storage.test_shared_gitea_uses_the_private_core_storage(True)
        reads = [call for call in self.calls if "/configreferences/" in call[1]]
        self.assertEqual(len(reads), 4)
        self.assertEqual(
            self.events,
            [f"delete {storage.strings.GITEA_SHARED_SERVICE}", f"install {storage.strings.GITEA_SHARED_SERVICE}"],
        )


if __name__ == "__main__":
    unittest.main()
