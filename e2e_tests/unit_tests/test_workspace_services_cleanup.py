import unittest
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient, MockTransport, Response

from e2e_tests import helpers
from e2e_tests import test_workspace_services as services
from e2e_tests.resources import resource, strings


class WorkspaceServicesCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, *, aad, install_failed=False, ping_failed=False, rejected=False, cleanup_failed=False):
        service_path = "/workspaces/reused/workspace-services/service"
        owned_path = "/workspaces/reused/workspace-services/owned" if aad else f"{service_path}/user-resources/owned"
        requests = []

        def handle(request):
            requests.append(request)
            if request.method == "POST":
                phase, code, state = "install", 403 if rejected else 202, "deploying"
            elif request.method == "PATCH":
                self.assertEqual(request.url.path, f"/api{owned_path}")
                self.assertEqual(request.content, b'{"isEnabled":false}')
                phase, code, state = "disable", 202, "updating"
            elif request.method == "DELETE":
                self.assertEqual(request.url.path, f"/api{owned_path}")
                phase, code, state = "delete", 200, "deleting"
            else:
                phase = request.url.path.rsplit("/", 1)[-1]
                code = 200
                state = {
                    "install": strings.RESOURCE_STATUS_DEPLOYMENT_FAILED if install_failed else "deployed",
                    "disable": strings.RESOURCE_STATUS_UPDATED,
                    "delete": strings.RESOURCE_STATUS_DELETING_FAILED if cleanup_failed else "deleted",
                }[phase]
            return Response(
                code,
                headers={"Location": f"/api/operations/{phase}"},
                json={
                    "operation": {
                        "resourcePath": owned_path,
                        "resourceId": "owned",
                        "status": state,
                        "message": "Mock operation",
                        "steps": [],
                    }
                },
            )

        ping = AsyncMock(side_effect=RuntimeError("ping failed") if ping_failed else None)
        with (
            patch.object(resource, "AsyncClient", side_effect=lambda **_: AsyncClient(transport=MockTransport(handle))),
            patch.object(helpers.config, "TRE_URL", "https://tre.example.test"),
            patch.object(services, "get_workspace_owner_token", new=AsyncMock(return_value="token")),
            patch.object(services, "ping_guacamole_workspace_service", new=ping),
        ):

            async def run():
                if aad:
                    await services.test_create_guacamole_service_into_aad_workspace(
                        ("/workspaces/reused", "reused"), True
                    )
                else:
                    await services.test_create_guacamole_service_into_base_workspace(
                        ("/workspaces/reused", "reused", service_path, "service"), True
                    )

            if ping_failed:
                with self.assertRaisesRegex(RuntimeError, "ping failed"):
                    await run()
            elif install_failed or rejected or cleanup_failed:
                with self.assertRaises(AssertionError):
                    await run()
            else:
                await run()

        expected = ["POST"] if rejected else ["POST", "GET", "PATCH", "GET", "DELETE", "GET"]
        if install_failed:
            expected.insert(2, "GET")
        self.assertEqual([request.method for request in requests], expected)

    async def test_success_removes_owned_resources_from_reused_parents(self):
        for aad in (False, True):
            with self.subTest(aad=aad):
                await self.run_case(aad=aad)

    async def test_failed_install_still_removes_owned_resources(self):
        for aad in (False, True):
            with self.subTest(aad=aad):
                await self.run_case(aad=aad, install_failed=True)

    async def test_failed_service_check_still_removes_owned_service(self):
        await self.run_case(aad=True, ping_failed=True)

    async def test_rejected_creation_does_not_delete(self):
        for aad in (False, True):
            with self.subTest(aad=aad):
                await self.run_case(aad=aad, rejected=True)

    async def test_cleanup_failure_is_reported(self):
        for aad in (False, True):
            with self.subTest(aad=aad):
                await self.run_case(aad=aad, cleanup_failed=True)
