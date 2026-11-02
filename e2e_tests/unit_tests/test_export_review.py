"""Exercise export review ordering, ownership and bounded recovery without Azure."""

import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
from types import SimpleNamespace
import unittest
from pathlib import Path

import yaml
from unittest.mock import AsyncMock, patch

from e2e_tests import test_airlock_export_review as export

WORKSPACE = "11111111-1111-4111-8111-111111111111"
SERVICE = "22222222-2222-4222-8222-222222222222"
REQUEST = "33333333-3333-4333-8333-333333333333"
SEED = "44444444-4444-4444-8444-444444444444"
REVIEW = "55555555-5555-4555-8555-555555555555"
SUBSCRIPTION = "00000000-0000-4000-8000-000000000000"
WORKSPACE_PATH = f"/workspaces/{WORKSPACE}"
SERVICE_PATH = f"{WORKSPACE_PATH}/workspace-services/{SERVICE}"


def token(roles):
    payload = base64.urlsafe_b64encode(json.dumps({"roles": roles}).encode()).decode().rstrip("=")
    return "header." + payload + ".signature"


class RoleTests(unittest.TestCase):
    def test_owner_and_airlock_manager_are_required_together(self):
        export.require_export_roles(token(["WorkspaceOwner", "AirlockManager"]))
        for roles in ([], ["WorkspaceOwner"], ["AirlockManager"], ["TREAdmin"], "WorkspaceOwner AirlockManager"):
            with self.subTest(roles=roles), self.assertRaisesRegex(ValueError, "WorkspaceOwner and AirlockManager"):
                export.require_export_roles(token(roles))


class ExportLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.identity = token(["WorkspaceOwner", "AirlockManager"])
        self.tamper_final = {"overwrite": 404, "add": 404, "delete": 404}
        self.tamper_attempts = 200
        self.tamper_answered = 200
        self.tamper_delay = 0
        # The real lead gives the Run Command time to start. A test needs no wait.
        self.enterContext(patch.object(export, "TAMPER_LEAD_SECONDS", 2))
        self.enterContext(patch.object(export, "TAMPER_CLOCK_MARGIN_SECONDS", 0))
        self.enterContext(
            patch.object(
                export,
                "assert_approved_copy",
                AsyncMock(side_effect=lambda *args: self.events.append("approved-copy")),
            )
        )
        self.enterContext(
            patch.dict(
                os.environ,
                ARM_SUBSCRIPTION_ID=SUBSCRIPTION,
                AZURE_ENVIRONMENT="AzureCloud",
                EXPORT_REVIEW_VALIDATION_DEADLINE="",
            )
        )
        for name, value in (
            ("TRE_ID", "tre-test"),
            ("TEST_WORKSPACE_APP_ID", "app"),
            ("TEST_WORKSPACE_APP_SECRET", "test-secret"),
            ("TEST_WORKSPACE_ID", "existing-workspace"),
            ("TEST_WORKSPACE_SERVICE_ID", "existing-service"),
        ):
            self.enterContext(patch.object(export.config, name, value))

        @asynccontextmanager
        async def nexus(verify):
            self.events.append("nexus")
            try:
                yield
            finally:
                self.events.append("delete-nexus")

        @asynccontextmanager
        async def seed(payload, endpoint, access_token, verify):
            self.assertEqual(endpoint, f"/api{SERVICE_PATH}/user-resources")
            self.events.append("seed")
            try:
                yield SERVICE_PATH + "/user-resources/" + SEED
            finally:
                self.events.append("delete-seed")

        @asynccontextmanager
        async def review(workspace_path, request_id, create_token, wait_token, verify):
            self.assertEqual(
                (workspace_path, request_id, create_token, wait_token),
                (WORKSPACE_PATH, REQUEST, self.identity, self.identity),
            )
            self.events.append("review")
            try:
                yield SERVICE_PATH + "/user-resources/" + REVIEW
            finally:
                self.events.append("recover-review")

        self.enterContext(patch.object(export, "nexus_prerequisites", nexus))
        self.enterContext(patch.object(export, "temporary_resource", seed))
        self.enterContext(patch.object(export, "managed_review_vm", review))
        self.create_workspace = self.enterContext(
            patch.object(export, "create_or_get_test_workspace", AsyncMock(return_value=(WORKSPACE_PATH, WORKSPACE)))
        )
        self.create_service = self.enterContext(
            patch.object(export, "create_or_get_test_workpace_service", AsyncMock(return_value=(SERVICE_PATH, SERVICE)))
        )
        self.enterContext(
            patch.object(export, "get_workspace_owner_token", AsyncMock(side_effect=lambda *_: self.identity))
        )
        self.enterContext(patch.object(export, "get_admin_token", AsyncMock(return_value="admin")))
        self.enterContext(
            patch.object(
                export,
                "clean_up_test_workspace",
                AsyncMock(side_effect=lambda *_: self.events.append("delete-workspace")),
            )
        )
        self.enterContext(
            patch.object(
                export,
                "clean_up_test_workspace_service",
                AsyncMock(side_effect=lambda *_: self.events.append("delete-service")),
            )
        )
        self.patch_workspace = self.enterContext(patch.object(export, "post_resource", AsyncMock()))
        self.wrong_owner = False
        self.wrong_template = False
        self.wrong_version = False
        self.wrong_registered = False
        self.fail_read = False
        self.fail_upload = False
        self.fail_deletion = False
        self.workspace_version = 2

        async def resource(endpoint, *_):
            if endpoint == "/api" + WORKSPACE_PATH:
                return {
                    "workspace": {
                        "_etag": "etag",
                        "properties": {
                            "airlock_version": self.workspace_version,
                            "airlock_review_config": {"import": {"existing": "preserved"}},
                        },
                    }
                }
            if "/user-resource-templates/" in endpoint:
                name = endpoint.rsplit("/", 1)[1]
                version = export.load_catalog()[name]["source_version"]
                return {"name": name, "version": "old" if self.wrong_registered else version}
            resource_id = endpoint.rsplit("/", 1)[1]
            template = (
                export.REVIEW_TEMPLATE if resource_id == REVIEW else export.strings.GUACAMOLE_WINDOWS_USER_RESOURCE
            )
            return {
                "userResource": {
                    "id": resource_id,
                    "templateName": "wrong" if self.wrong_template else template,
                    "deploymentStatus": "deployed",
                    "templateVersion": "old"
                    if self.wrong_version
                    else export.load_catalog()[template]["source_version"],
                    "properties": {"azure_resource_id": self.vm_id(resource_id)},
                }
            }

        async def arm_request(method, identifier, api):
            resource_id = identifier.rsplit("/", 1)[1]
            return {
                "id": identifier,
                "location": "westeurope",
                "identity": {"principalId": "principal"},
                "tags": {
                    "tre_id": "tre-test",
                    "tre_workspace_id": "other" if self.wrong_owner else WORKSPACE,
                    "tre_workspace_service_id": SERVICE,
                    "tre_user_resource_id": resource_id,
                },
            }

        self.arm = SimpleNamespace(request=AsyncMock(side_effect=arm_request))

        @asynccontextmanager
        async def arm():
            yield self.arm

        self.enterContext(patch.object(export, "get_resource", resource))
        self.enterContext(patch.object(export, "arm_client", arm))

        async def post(payload, endpoint, *args):
            if endpoint.endswith("/submit"):
                self.events.append("submit")
                self.submitted_at = export.datetime.now(export.timezone.utc)
                return {"airlockRequest": {"status": "submitted"}}
            if endpoint.endswith("/review"):
                self.events.append("approve")
                return {}
            self.events.append("draft")
            return {"airlockRequest": {"type": "export", "status": "draft", "id": REQUEST}}

        async def probe(*args, phase, **kwargs):
            self.events.append(phase)
            if (phase == "read" and self.fail_read) or (phase == "upload" and self.fail_upload):
                raise AssertionError("probe failed")
            if phase in ("upload", "tamper"):
                self.assertIn("container_url", kwargs)
            else:
                self.assertNotIn("container_url", kwargs)
            if phase == "tamper":
                # The probe runs across the submit. Its first write attempt must come after it.
                while "submit" not in self.events:
                    await asyncio.sleep(0)
                self.assertGreater(kwargs["start_at"], self.submitted_at)
                # The probe gets its start time in whole seconds.
                self.assertEqual(kwargs["start_at"].microsecond, 0)
                return {
                    "accepted_writes": 0,
                    "answered_writes": self.tamper_answered,
                    "attempted_writes": self.tamper_attempts,
                    "start_delay_seconds": self.tamper_delay,
                    "final": self.tamper_final,
                }

        async def deletion(*args):
            self.events.append("automatic-delete")
            if self.fail_deletion:
                raise TimeoutError("automatic deletion stalled")

        self.enterContext(patch.object(export, "post_request", post))
        self.enterContext(
            patch.object(
                export,
                "get_request",
                AsyncMock(
                    return_value={
                        "containerUrl": f"https://stalairlockgtretest.blob.core.windows.net/{REQUEST}-draft?sig=test"
                    }
                ),
            )
        )
        self.enterContext(
            patch.object(export, "wait_for_status", AsyncMock(side_effect=lambda state, *_: self.events.append(state)))
        )
        self.probe = self.enterContext(patch.object(export, "run_probe", AsyncMock(side_effect=probe)))
        self.enterContext(patch.object(export, "wait_for_review_vm_deletion", deletion))

    def vm_id(self, resource_id):
        return f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-tre-test-ws-1111/providers/Microsoft.Compute/virtualMachines/{resource_id}"

    async def test_upload_review_approval_and_cleanup_order(self):
        await export.test_airlock_export_review_vm_flow(True)
        self.assertEqual(
            self.events,
            [
                "nexus",
                "seed",
                "draft",
                "upload",
                "tamper",
                "submit",
                "in_review",
                "review",
                "read",
                "approve",
                "approved",
                "approved-copy",
                "automatic-delete",
                "recover-review",
                "delete-seed",
                "delete-service",
                "delete-workspace",
                "delete-nexus",
            ],
        )
        self.assertEqual(self.create_workspace.await_args.kwargs["pre_created_workspace_id"], "")
        self.assertEqual(self.create_service.await_args.args[2], "")
        settings = self.patch_workspace.await_args.args[0]["properties"]
        self.assertEqual(settings["airlock_review_config"]["import"], {"existing": "preserved"})
        self.assertEqual(settings["airlock_review_config"]["export"]["export_vm_workspace_service_id"], SERVICE)
        self.assertEqual(self.patch_workspace.await_args.args[2], "admin")
        calls = {call.kwargs["phase"]: call.kwargs for call in self.probe.await_args_list}
        for phase in ("tamper", "read"):
            with self.subTest(phase=phase):
                self.assertEqual(calls[phase]["blob_name"], calls["upload"]["blob_name"])
                self.assertEqual(calls[phase]["content"], calls["upload"]["content"])
        self.assertEqual(calls["tamper"]["container_url"], calls["upload"]["container_url"])

    async def test_a_tamper_probe_without_writes_in_time_fails_before_review(self):
        for attempts, delay, message in (
            (0, 0, "answered no write"),
            (200, 60, "started 60 s late. The limit is 30 s"),
        ):
            with self.subTest(attempts=attempts, delay=delay):
                self.events.clear()
                self.tamper_attempts, self.tamper_answered, self.tamper_delay = attempts, attempts, delay
                with self.assertRaisesRegex(AssertionError, message):
                    await export.test_airlock_export_review_vm_flow(True)
                self.assertNotIn("review", self.events)

    async def test_tamper_writes_without_an_http_answer_fail_before_review(self):
        # Each write timed out or found no connection, so no write shows what the storage does with the old SAS.
        self.tamper_answered = 0
        with self.assertRaisesRegex(AssertionError, "The storage answered no write through the old draft SAS"):
            await export.test_airlock_export_review_vm_flow(True)
        self.assertNotIn("review", self.events)

    async def test_an_old_draft_sas_that_still_writes_after_the_submit_fails_before_review(self):
        self.tamper_final = {"overwrite": 201, "add": 404, "delete": 404}
        with self.assertRaisesRegex(AssertionError, "still changes data after the submit"):
            await export.test_airlock_export_review_vm_flow(True)
        self.assertNotIn("review", self.events)
        self.assertNotIn("approve", self.events)

    async def test_a_submit_inside_the_clock_margin_fails_before_review(self):
        # The test lead is 2 seconds, so a margin of 10 seconds puts the end of the submit inside the margin.
        with patch.object(export, "TAMPER_CLOCK_MARGIN_SECONDS", 10):
            with self.assertRaisesRegex(AssertionError, "less than 10 s before the planned start"):
                await export.test_airlock_export_review_vm_flow(True)
        self.assertNotIn("review", self.events)

    async def test_an_answer_that_is_not_a_refusal_fails_before_review(self):
        # A server error or no HTTP answer does not show that the old SAS lost access.
        for code in (0, 500, 503, 400):
            with self.subTest(code=code):
                self.events.clear()
                self.tamper_final = {"overwrite": 404, "add": code, "delete": 409}
                with self.assertRaisesRegex(AssertionError, "got no refusal from the storage"):
                    await export.test_airlock_export_review_vm_flow(True)
                self.assertNotIn("review", self.events)

    async def test_a_registered_vm_version_that_differs_stops_before_any_resource(self):
        self.wrong_registered = True
        with self.assertRaisesRegex(ValueError, "Register the checkout version of tre-service-guacamole-windowsvm"):
            await export.test_airlock_export_review_vm_flow(True)
        # The check comes before Nexus, the workspace and the VMs.
        self.assertEqual(self.events, [])
        self.create_workspace.assert_not_awaited()

    async def test_failed_upload_never_submits_and_cleans_owned_resources(self):
        self.fail_upload = True
        with self.assertRaisesRegex(AssertionError, "probe failed"):
            await export.test_airlock_export_review_vm_flow(True)
        self.assertNotIn("submit", self.events)
        self.assertEqual(self.events[-4:], ["delete-seed", "delete-service", "delete-workspace", "delete-nexus"])

    async def test_wrong_review_data_never_approves_and_still_recovers(self):
        self.fail_read = True
        with self.assertRaisesRegex(AssertionError, "probe failed"):
            await export.test_airlock_export_review_vm_flow(True)
        self.assertNotIn("approve", self.events)
        self.assertEqual(
            self.events[-5:], ["recover-review", "delete-seed", "delete-service", "delete-workspace", "delete-nexus"]
        )

    async def test_a_stalled_review_vm_recovery_keeps_cleanup_time_for_the_other_resources(self):
        from e2e_tests import test_airlock as airlock

        async def stall(*args, **kwargs):
            await asyncio.Event().wait()

        async def recover(*args):
            self.events.append("recover-review")
            await asyncio.Event().wait()

        review = AsyncMock(return_value=(SERVICE_PATH + "/user-resources/" + REVIEW, REVIEW))
        with (
            patch.object(export, "LIFECYCLE_SECONDS", 2.0),
            patch.object(export, "CLEANUP_RESERVE_SECONDS", 1.5),
            # The real helper. Its recovery has a limit of 60 minutes and continues after a cancellation.
            patch.object(export, "managed_review_vm", airlock.managed_review_vm),
            patch.object(airlock, "post_resource", review),
            patch.object(airlock, "delete_owned_resource_if_present", recover),
            # The work deadline expires while the case waits for the automatic deletion.
            patch.object(export, "wait_for_review_vm_deletion", stall),
            self.assertRaises(TimeoutError),
        ):
            await export.test_airlock_export_review_vm_flow(True)
        # The review VM recovery stops at the end of its share. The other resources still get their cleanup.
        self.assertEqual(
            self.events[-5:], ["recover-review", "delete-seed", "delete-service", "delete-workspace", "delete-nexus"]
        )

    async def test_a_failed_workspace_creation_keeps_the_client_secret_out_of_the_failure_report(self):
        import _pytest._code
        import httpx

        from e2e_tests import conftest as fixtures

        secret = os.urandom(16).hex()

        @asynccontextmanager
        async def failed_template(*args):
            yield httpx.Response(500, text="error")

        with (
            patch.object(export.config, "TEST_WORKSPACE_APP_SECRET", secret),
            # The real helper, so that the report has its frames and arguments.
            patch.object(export, "create_or_get_test_workspace", fixtures.create_or_get_test_workspace),
            patch.object(fixtures, "get_template", failed_template),
            patch.object(fixtures, "get_admin_token", AsyncMock(return_value="admin")),
        ):
            try:
                await export.test_airlock_export_review_vm_flow(True)
            except AssertionError as error:
                # pytest shows the arguments of the first and last frames. This report shows them for all frames.
                report = str(_pytest._code.ExceptionInfo.from_exception(error).getrepr(funcargs=True, style="long"))
            else:
                self.fail("A failed template read must fail the case")
        self.assertIn("Failed to GET workspace template", report)
        self.assertNotIn(secret, report)
        self.assertIn("client_secret = '<hidden>'", report)

    async def test_fallback_deletion_does_not_make_failed_automatic_deletion_pass(self):
        self.fail_deletion = True
        with self.assertRaisesRegex(TimeoutError, "automatic deletion stalled"):
            await export.test_airlock_export_review_vm_flow(True)
        self.assertIn("recover-review", self.events)

    async def test_unrelated_vm_or_wrong_template_is_rejected_before_run_command(self):
        for field in ("wrong_owner", "wrong_template", "wrong_version"):
            setattr(self, field, True)
            with self.subTest(field=field), self.assertRaises((ValueError, AssertionError)):
                await export.test_airlock_export_review_vm_flow(True)
            self.probe.assert_not_awaited()
            setattr(self, field, False)

    async def test_missing_review_role_fails_before_service_or_vm_creation(self):
        self.identity = token(["WorkspaceOwner"])
        with self.assertRaisesRegex(ValueError, "AirlockManager"):
            await export.test_airlock_export_review_vm_flow(True)
        self.create_service.assert_not_awaited()
        self.probe.assert_not_awaited()
        self.assertEqual(self.events, ["nexus", "delete-workspace", "delete-nexus"])

    async def test_legacy_workspace_cannot_provide_v2_export_evidence(self):
        self.workspace_version = 1
        with self.assertRaisesRegex(AssertionError, "Airlock v2"):
            await export.test_airlock_export_review_vm_flow(True)
        self.patch_workspace.assert_not_awaited()
        self.probe.assert_not_awaited()


class WorkflowDeadlineTests(unittest.TestCase):
    def test_bundle_job_sets_and_passes_the_deadline_before_container_start(self):
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.safe_load((root / ".github/workflows/deploy_tre_reusable.yml").read_text())
        job = workflow["jobs"]["e2e_tests_bundle"]
        self.assertEqual(job["timeout-minutes"], 300)
        deadline = next(
            step for step in job["steps"] if step["name"] == "Reserve export review cleanup and reporting time"
        )
        run = next(step for step in job["steps"] if step["name"] == "Run E2E Tests")
        self.assertEqual(deadline["if"], "inputs.e2eBundle == 'tre-service-guacamole-export-reviewvm'")
        self.assertIn("270 * 60", deadline["run"])
        self.assertLess(job["steps"].index(deadline), job["steps"].index(run))
        self.assertIn(
            "EXPORT_REVIEW_VALIDATION_DEADLINE=${{ env.EXPORT_REVIEW_VALIDATION_DEADLINE }}", run["with"]["COMMAND"]
        )


class ExportDeadlineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, AZURE_ENVIRONMENT="AzureCloud", EXPORT_REVIEW_VALIDATION_DEADLINE=""))
        self.enterContext(patch.object(export.config, "TEST_WORKSPACE_APP_ID", "app"))
        self.enterContext(patch.object(export.config, "TEST_WORKSPACE_APP_SECRET", "test-secret"))
        self.events = []

        @asynccontextmanager
        async def nexus(verify):
            self.events.append("nexus")
            try:
                yield
            finally:
                self.events.append("cleanup")

        self.enterContext(patch.object(export, "nexus_prerequisites", nexus))

    async def test_work_timeout_keeps_cleanup_time(self):
        async def cleanup():
            await asyncio.sleep(0.04)
            self.events.append("owned-cleanup")

        with patch.object(export, "LIFECYCLE_SECONDS", 0.2), patch.object(export, "CLEANUP_RESERVE_SECONDS", 0.18):
            with self.assertRaises(TimeoutError):
                async with export.export_resources(True) as resources:
                    resources.push_async_callback(cleanup)
                    await asyncio.sleep(1)
        self.assertEqual(self.events, ["nexus", "owned-cleanup", "cleanup"])

    async def test_cleanup_failure_preserves_original_failure(self):
        async def fail():
            raise RuntimeError("cleanup failed")

        original = ValueError("body failed")
        with self.assertRaises(ValueError) as caught:
            async with export.export_resources(True) as resources:
                resources.push_async_callback(fail)
                raise original
        self.assertIs(caught.exception, original)
        self.assertIn("cleanup failed", str(original.__notes__))
        self.assertEqual(self.events[-1], "cleanup")

    async def test_stalled_cleanup_keeps_time_for_later_resources(self):
        events = []

        async def stall():
            events.append("stall")
            await asyncio.Event().wait()

        async def finish():
            events.append("later")

        resources = export.ExportResources(asyncio.get_running_loop().time() + 0.12)
        resources.push_async_callback(finish)
        resources.push_async_callback(stall)
        with self.assertRaises(TimeoutError):
            await resources.aclose()
        self.assertEqual(events, ["stall", "later"])
        self.assertEqual(resources.pending, 0)

    async def test_invalid_or_expired_deadline_fails_before_nexus(self):
        for value in ("nan", "inf", "-inf", "1", "invalid"):
            with self.subTest(deadline=value), patch.dict(os.environ, EXPORT_REVIEW_VALIDATION_DEADLINE=value):
                with self.assertRaises((ValueError, TimeoutError)):
                    async with export.export_resources(True):
                        self.fail("The body must not start")
        self.assertEqual(self.events, [])

    async def test_missing_manual_app_fails_before_resource_creation(self):
        with (
            patch.object(export.config, "TEST_WORKSPACE_APP_ID", ""),
            self.assertRaisesRegex(ValueError, "manual test workspace"),
        ):
            async with export.export_resources(True):
                self.fail("The body must not start")
        self.assertEqual(self.events, [])


class ApprovedCopyTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_approved_copy_reads_keep_the_sas_out_of_the_log(self):
        import hashlib
        import logging

        import httpx

        content = b"Synthetic export"
        transport = httpx.MockTransport(
            lambda request: (
                httpx.Response(200, text="<Name>a.txt</Name>")
                if "comp=list" in str(request.url)
                else httpx.Response(200, content=content)
            )
        )
        link = {"containerUrl": f"https://stalairlocktre.blob.core.windows.net/{REQUEST}?sig=secret-sas-value"}
        with (
            patch.object(export, "get_request", AsyncMock(return_value=link)),
            patch.object(export, "AsyncClient", lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)),
            self.assertLogs(level=logging.DEBUG) as logs,
        ):
            await export.assert_approved_copy("/endpoint", "token", True, "a.txt", hashlib.sha256(content).hexdigest())
        self.assertNotIn("secret-sas-value", "\n".join(logs.output))
        self.assertEqual(logging.getLogger("httpx").level, logging.NOTSET)

    async def test_a_failed_approved_copy_check_keeps_the_sas_out_of_the_failure_report(self):
        import _pytest._code
        import httpx

        transport = httpx.MockTransport(lambda request: httpx.Response(403))
        signature = os.urandom(16).hex()
        link = {"containerUrl": f"https://stalairlocktre.blob.core.windows.net/{REQUEST}?sv=2024&sig={signature}"}
        with (
            patch.object(export, "get_request", AsyncMock(return_value=link)),
            patch.object(export, "AsyncClient", lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)),
        ):
            try:
                await export.assert_approved_copy("/endpoint", "token", True, "a.txt", "hash")
            except AssertionError as error:
                # pytest makes this report for the job log and the JUnit file. Only the frames of the case count,
                # because this test has the signature in its own variables.
                info = _pytest._code.ExceptionInfo.from_exception(error)
                info.traceback = info.traceback.filter(lambda entry: Path(entry.path) == Path(export.__file__))
                report = str(info.getrepr(funcargs=True, showlocals=True, style="long"))
            else:
                self.fail("A refused listing must fail the check")
        self.assertIn("Listing the approved export failed with HTTP 403", report)
        self.assertNotIn(signature, report)

    async def test_the_approved_copy_must_hold_the_submitted_file_only(self):
        import hashlib

        import httpx

        content = b"Synthetic export"
        expected = hashlib.sha256(content).hexdigest()
        cases = (
            ("<Name>a.txt</Name>", content, None),
            ("<Name>a.txt</Name><Name>added-a.txt</Name>", content, "other files"),
            ("<Name>a.txt</Name>", b"Changed", "changed"),
        )
        for listing, blob, error in cases:

            def respond(request, listing=listing, blob=blob):
                return (
                    httpx.Response(200, text=listing)
                    if "comp=list" in str(request.url)
                    else httpx.Response(200, content=blob)
                )

            transport = httpx.MockTransport(respond)
            link = {"containerUrl": f"https://stalairlocktre.blob.core.windows.net/{REQUEST}?sig=test"}
            with (
                self.subTest(error=error),
                patch.object(export, "get_request", AsyncMock(return_value=link)),
                patch.object(export, "AsyncClient", lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)),
            ):
                if error:
                    with self.assertRaisesRegex(AssertionError, error):
                        await export.assert_approved_copy("/endpoint", "token", True, "a.txt", expected)
                else:
                    await export.assert_approved_copy("/endpoint", "token", True, "a.txt", expected)
