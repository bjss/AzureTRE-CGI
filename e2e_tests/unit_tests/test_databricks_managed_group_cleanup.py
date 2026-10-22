"""Run the Databricks managed resource group clean-up from both Databricks bundles against a fake Azure CLI."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUNDLES = (
    ROOT / "templates/shared_services/databricks-auth/terraform/main.tf",
    ROOT / "templates/workspace_services/databricks/terraform/main.tf",
)
GROUP = "rg-adb-tre-svc-1234"
WORKSPACE = (
    "/subscriptions/sub/resourceGroups/rg-tre-svc-1234/providers/Microsoft.Databricks/workspaces/adb-tre-svc-1234"
)

FAKE_AZ = """import json,os,sys
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
state=json.load(open(os.environ['STATE']))
if args[:2]==['resource','show']:sys.exit(0 if state['workspace'] else 3)
if args[:2]==['group','exists']:print('true' if state['group'] else 'false')
if args[:2]==['group','show']:print(state['managed_by'])
if args[:2]==['group','delete']:
    state['group']=False;json.dump(state,open(os.environ['STATE'],'w'))
"""


def block(path):
    return (
        path.read_text().split('resource "terraform_data" "managed_resource_group_cleanup" {', 1)[1].split("\n}", 1)[0]
    )


def cleanup_command(path):
    command = block(path).split("<<EOT\n", 1)[1].split("\nEOT", 1)[0]
    return (
        command.replace("${self.input.subscription_id}", "sub")
        .replace("${self.input.managed_resource_group_name}", GROUP)
        .replace("${self.input.workspace_id}", WORKSPACE)
    )


class ManagedGroupCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        path = self.folder / "az"
        path.write_text("#!" + sys.executable + "\n" + FAKE_AZ)
        path.chmod(0o755)

    def run_cleanup(self, path, *, workspace=False, group=True, managed_by=WORKSPACE):
        state = self.folder / "state.json"
        state.write_text(json.dumps({"workspace": workspace, "group": group, "managed_by": managed_by}))
        calls_file = self.folder / "calls.jsonl"
        calls_file.unlink(missing_ok=True)
        env = os.environ | {
            "PATH": str(self.folder) + os.pathsep + os.environ["PATH"],
            "CALLS": str(calls_file),
            "STATE": str(state),
        }
        result = subprocess.run(
            ["bash", "-c", cleanup_command(path)], env=env, capture_output=True, text=True, timeout=20
        )
        calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
        deletes = [call for call in calls if call[:2] == ["group", "delete"]]
        return result, deletes, json.loads(state.read_text())

    def test_group_left_by_databricks_is_deleted(self):
        for path in BUNDLES:
            with self.subTest(path=path.parent.parent.name):
                result, deletes, state = self.run_cleanup(path, managed_by=WORKSPACE.lower())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(deletes, [["group", "delete", "--name", GROUP, "--yes"]])
                self.assertFalse(state["group"])

    def test_group_of_an_existing_workspace_stays(self):
        for path in BUNDLES:
            with self.subTest(path=path.parent.parent.name):
                result, deletes, state = self.run_cleanup(path, workspace=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(deletes, [])
                self.assertTrue(state["group"])

    def test_group_deleted_by_databricks_needs_no_change(self):
        for path in BUNDLES:
            with self.subTest(path=path.parent.parent.name):
                result, deletes, _ = self.run_cleanup(path, group=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(deletes, [])

    def test_group_managed_by_another_resource_stays_and_fails_the_destroy(self):
        for path in BUNDLES:
            with self.subTest(path=path.parent.parent.name):
                result, deletes, state = self.run_cleanup(path, managed_by=WORKSPACE + "-other")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(deletes, [])
                self.assertTrue(state["group"])

    def test_clean_up_runs_after_the_workspace_delete(self):
        for path in BUNDLES:
            with self.subTest(path=path.parent.parent.name):
                source = path.read_text()
                workspace = source.split('resource "azurerm_databricks_workspace" "databricks" {', 1)[1]
                depends_on = workspace.split("depends_on = [", 1)[1].split("]", 1)[0]
                self.assertIn("terraform_data.managed_resource_group_cleanup", depends_on)
                self.assertRegex(block(path), r"when\s*=\s*destroy")
                self.assertRegex(
                    block(path), re.escape("managed_resource_group_name = local.managed_resource_group_name")
                )


if __name__ == "__main__":
    unittest.main()
