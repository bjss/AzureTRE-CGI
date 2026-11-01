"""Retry the core Terraform init after a delayed role on the state storage account, with mocked commands."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

# Diagnostic of the first core init in a new CI environment, about 10 minutes after the bootstrap gave the role.
WORKSPACE_PERMISSION_ERROR = (
    "Error: Failed to get existing workspaces: listing blobs: executing request: unexpected status 403 "
    "(403 This request is not authorized to perform this operation using this permission.) "
    "with AuthorizationPermissionMismatch: This request is not authorized to perform this operation "
    "using this permission."
)
# Diagnostic of a bootstrap init that locked the state and then got HTTP 403 when it released the lock.
UNLOCK_PERMISSION_ERROR = (
    "Error: Error loading state: Error unlocking Azure state. Lock ID: 32d1662a-8852-e344-b7ce-5f1c3b76e688\n\n"
    "Error: failed to retrieve lock info: executing request: unexpected status 403 "
    "(403 This request is not authorized to perform this operation using this permission.) with EOF"
)

MOCK_COMMAND = r"""
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["MOCK_ROOT"])
config = json.loads((root / "config.json").read_text())
command = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps([command, *args]) + "\n")

if command == "terraform" and args[0] == "init":
    counter = root / "init_count"
    index = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(index + 1))
    responses = config.get("init_responses", [])
    if index < len(responses) and responses[index]:
        print(responses[index], file=sys.stderr)
        sys.exit(1)
    print("Terraform has been successfully initialized!")
elif command == "terraform" and args[0] == "show":
    print(json.dumps({"values": {"root_module": {}}}))
elif command == "script":
    Path(args[-1]).write_text("Script done.\n")
elif command == "az":
    # The core resource group does not exist, so the migration imports nothing.
    sys.exit(3)
"""


class CoreTerraformInitTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        mock = self.root / "mock.py"
        mock.write_text(f"#!{sys.executable}\n" + MOCK_COMMAND)
        mock.chmod(0o755)
        for command in ("terraform", "az", "sleep", "script"):
            (bin_dir / command).symlink_to(mock)
        self.core_dir = self.root / "core/terraform"
        self.core_dir.mkdir(parents=True)
        shutil.copy2(ROOT / "core/terraform/migrate.sh", self.core_dir)
        (self.root / "templates/shared_services/firewall/terraform").mkdir(parents=True)
        scripts = self.root / "devops/scripts"
        scripts.mkdir(parents=True)
        for name in ("terraform_init.sh", "terraform_wrapper.sh"):
            shutil.copy2(ROOT / "devops/scripts" / name, scripts)
        # Network access to the storage account is outside these tests.
        (scripts / "storage_enable_public_access.sh").write_text(":\n")
        self.env = {
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(self.root),
            "MOCK_ROOT": str(self.root),
            "TRE_ID": "tretest",
            "LOCATION": "mock-location",
            "ARM_SUBSCRIPTION_ID": "mock-subscription",
            "TF_VAR_mgmt_resource_group_name": "rg-mgmt",
            "TF_VAR_mgmt_storage_account_name": "mgmtstorage",
            "TF_VAR_terraform_state_container_name": "tfstate",
        }

    def run_script(self, *command, init_responses=()):
        (self.root / "config.json").write_text(json.dumps({"init_responses": list(init_responses)}))
        (self.root / "calls.jsonl").write_text("")
        (self.root / "init_count").unlink(missing_ok=True)
        result = subprocess.run(
            ["/bin/bash", *command],
            cwd=self.core_dir,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.calls = [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]
        self.output = result.stdout + result.stderr
        return result

    def run_migration(self, init_responses=()):
        return self.run_script("migrate.sh", init_responses=init_responses)

    def run_wrapper(self, init_responses=()):
        return self.run_script(
            "../../devops/scripts/terraform_wrapper.sh",
            *("-d", str(self.core_dir), "-g", "rg-mgmt", "-s", "mgmtstorage", "-n", "tfstate"),
            *("-k", "tretest", "-l", "tf.log", "-c", "terraform plan"),
            init_responses=init_responses,
        )

    def commands(self, *prefix):
        return [call for call in self.calls if call[: len(prefix)] == list(prefix)]

    def init_keys(self):
        return [next(arg for arg in call if arg.startswith("-backend-config=key=")) for call in self.inits()]

    def inits(self):
        return self.commands("terraform", "init")

    def sleeps(self):
        return [int(call[1]) for call in self.commands("sleep")]

    def indexes(self, *prefix):
        return [index for index, call in enumerate(self.calls) if call[: len(prefix)] == list(prefix)]

    def test_core_init_retries_a_permission_failure_before_the_state_lock(self):
        result = self.run_migration([WORKSPACE_PERMISSION_ERROR])
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn(WORKSPACE_PERMISSION_ERROR, self.output)
        self.assertEqual(self.sleeps(), [10])
        self.assertEqual(
            self.init_keys(),
            [
                "-backend-config=key=tretest",
                "-backend-config=key=tretest",
                "-backend-config=key=tretest-shared-service-firewall",
            ],
        )
        for call in self.inits():
            self.assertIn("-backend-config=storage_account_name=mgmtstorage", call)
        self.assertIn("-upgrade", self.inits()[-1])
        # The migration reads the state only after a successful init.
        self.assertLess(self.indexes("terraform", "init")[1], self.indexes("terraform", "state", "list")[0])

    def test_firewall_state_init_retries_a_permission_failure(self):
        result = self.run_migration([None, WORKSPACE_PERMISSION_ERROR])
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.sleeps(), [10])
        self.assertEqual(
            self.init_keys(),
            [
                "-backend-config=key=tretest",
                "-backend-config=key=tretest-shared-service-firewall",
                "-backend-config=key=tretest-shared-service-firewall",
            ],
        )

    def test_core_init_stops_after_about_five_minutes_of_permission_failures(self):
        result = self.run_migration([WORKSPACE_PERMISSION_ERROR] * 6)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.inits()), 6)
        self.assertEqual(self.sleeps(), [10, 20, 40, 80, 160])
        self.assertIn("ERROR: Terraform backend initialisation failed.", self.output)
        self.assertEqual(self.commands("terraform", "state"), [])
        self.assertEqual(self.commands("terraform", "refresh"), [])

    def test_core_init_does_not_retry_a_lock_release_failure(self):
        result = self.run_migration([UNLOCK_PERMISSION_ERROR])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Check the reported lock owner", self.output)
        self.assertEqual(len(self.inits()), 1)
        self.assertEqual(self.sleeps(), [])
        self.assertEqual(self.commands("terraform", "state"), [])

    def test_core_init_does_not_retry_other_errors(self):
        for error in ("Error: Invalid backend configuration", "Error: Error loading state: HTTP 403"):
            with self.subTest(error=error):
                result = self.run_migration([error])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, self.output)
                self.assertEqual(len(self.inits()), 1)
                self.assertEqual(self.sleeps(), [])

    def test_wrapper_init_retries_a_permission_failure_before_the_command(self):
        result = self.run_wrapper([WORKSPACE_PERMISSION_ERROR])
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.init_keys(), ["-backend-config=key=tretest", "-backend-config=key=tretest"])
        self.assertEqual(self.sleeps(), [10])
        self.assertEqual(self.commands("script"), [["script", "-c", "terraform plan", "tf.log"]])
        self.assertLess(self.indexes("terraform", "init")[-1], self.indexes("script")[0])

    def test_wrapper_does_not_run_the_command_after_a_lock_release_failure(self):
        result = self.run_wrapper([UNLOCK_PERMISSION_ERROR])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Check the reported lock owner", self.output)
        self.assertEqual(len(self.inits()), 1)
        self.assertEqual(self.sleeps(), [])
        self.assertEqual(self.commands("script"), [])


if __name__ == "__main__":
    unittest.main()
