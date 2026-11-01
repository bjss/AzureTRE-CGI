"""Exercise the resource processor reimage step with a mocked Azure CLI and a mocked clock."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[2] / "core/terraform/resource_processor/vmss_porter"
SCRIPT = MODULE / "reimage_instances.sh"
LOGIN_LIFETIME_SECONDS = 40 * 60

# The mocked "az" returns scripted instance states. Each action (reimage, delete-instances and scale)
# starts a new list of states, and the last item of a list repeats.
MOCK_AZ = r"""
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["MOCK_ROOT"])
config = json.loads((root / "config.json").read_text())
state_file = root / "state.json"
state = json.loads(state_file.read_text()) if state_file.exists() else {"phase": "start", "index": 0}
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps({"clock": int((root / "clock").read_text()), "args": args}) + "\n")


def option(name):
    return args[args.index(name) + 1] if name in args else None


def finish(code=0, output=""):
    state_file.write_text(json.dumps(state))
    if output:
        print(output, file=sys.stderr if code else sys.stdout)
    sys.exit(code)


if option("--name") == "vmss-mock" and option("--resource-group") == "rg-mock":
    action = args[1]
    tsv = option("--output") == "tsv"
    if action == "list-instances" and tsv and option("--query") == "[].[instanceId, provisioningState]":
        states = config[state["phase"]]
        current = states[min(state["index"], len(states) - 1)]
        state["index"] += 1
        finish(output="\n".join(instance_id + "\t" + value for instance_id, value in current))
    if action == "show" and tsv and option("--query") == "sku.capacity":
        finish(output=str(config["capacity"]))
    if action in ("reimage", "delete-instances", "scale") and "--no-wait" in args:
        state["phase"], state["index"] = action, 0
        finish()
finish(99, "Unexpected mocked command: " + repr(args))
"""

# The mocked "sleep" moves a clock forward, and the mocked "date" prints this clock.
# So the tests use the default time limits, and they do not wait.
MOCK_SLEEP = '#!/bin/bash\necho $(($(<"$MOCK_ROOT/clock") + $1)) >"$MOCK_ROOT/clock"\n'
MOCK_DATE = '#!/bin/bash\n[ "$*" = "+%s" ] || exit 99\necho $((1800000000 + $(<"$MOCK_ROOT/clock")))\n'

ONE_GOOD = [["0", "Succeeded"]]


class ReimageInstancesTests(unittest.TestCase):
    def run_script(self, fake_clock=True, environment=None, **scenario):
        config = {
            "capacity": 1,
            "start": [ONE_GOOD],
            "reimage": [[["0", "Updating"]], ONE_GOOD],
            "delete-instances": [[["0", "Deleting"]], []],
            "scale": [[["1", "Creating"]], [["1", "Succeeded"]]],
            **scenario,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(json.dumps(config))
            (root / "clock").write_text("0")
            mocks = {"az": f"#!{sys.executable} -S\n" + MOCK_AZ}
            if fake_clock:
                mocks.update(sleep=MOCK_SLEEP, date=MOCK_DATE)
            for name, content in mocks.items():
                executable = root / name
                executable.write_text(content)
                executable.chmod(0o755)
            env = {
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "MOCK_ROOT": str(root),
                "VMSS_NAME": "vmss-mock",
                "RESOURCE_GROUP": "rg-mock",
                **(environment or {}),
            }
            result = subprocess.run(
                ["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, check=False
            )
            calls_file = root / "calls.jsonl"
            calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
            self.clock = int((root / "clock").read_text())
        self.az_calls = [(call["clock"], call["args"]) for call in calls]
        self.output = result.stdout + result.stderr
        return result

    def actions(self):
        return [args[1] for _, args in self.az_calls]

    def action_call(self, action):
        matches = [(clock, args) for clock, args in self.az_calls if args[1] == action]
        self.assertEqual(len(matches), 1, self.output)
        return matches[0]

    def assert_replaced(self, instance_ids, capacity, at_clock):
        clock, args = self.action_call("delete-instances")
        self.assertEqual(clock, at_clock)
        self.assertEqual(args[args.index("--instance-ids") + 1 : args.index("--no-wait")], instance_ids)
        _, args = self.action_call("scale")
        self.assertEqual(args[args.index("--new-capacity") + 1], str(capacity))

    def assert_repair_message(self, result):
        self.assertEqual(result.returncode, 1, self.output)
        self.assertIn("ERROR:", result.stderr)
        self.assertIn("scale set vmss-mock in resource group rg-mock", result.stderr)
        self.assertIn("docs/troubleshooting-faq/troubleshooting-rp.md", result.stderr)

    def test_normal_reimage_does_not_replace(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.actions(), ["list-instances", "reimage", "list-instances", "list-instances"])
        self.assertIn("The reimage is complete.", result.stdout)
        self.assertEqual(self.clock, 40)

    def test_busy_instance_before_reimage_gets_time_to_finish(self):
        result = self.run_script(start=[[["0", "Updating"]]] * 3 + [ONE_GOOD])
        self.assertEqual(result.returncode, 0, self.output)
        clock, _ = self.action_call("reimage")
        self.assertEqual(clock, 60)
        self.assertNotIn("delete-instances", self.actions())

    def test_instance_stuck_before_reimage_is_replaced_without_reimage(self):
        result = self.run_script(start=[[["0", "Updating"]]])
        self.assertEqual(result.returncode, 0, self.output)
        self.assertNotIn("reimage", self.actions())
        self.assert_replaced(["0"], 1, at_clock=300)
        self.assertIn("The replace is complete.", result.stdout)

    def test_failed_instance_before_reimage_replaces_all_instances_at_once(self):
        result = self.run_script(
            capacity=2,
            start=[[["0", "Succeeded"], ["1", "Failed"]]],
            scale=[[["2", "Succeeded"], ["3", "Succeeded"]]],
        )
        self.assertEqual(result.returncode, 0, self.output)
        self.assertNotIn("reimage", self.actions())
        self.assert_replaced(["0", "1"], 2, at_clock=0)

    def test_instance_stuck_after_reimage_is_replaced_when_the_wait_ends(self):
        result = self.run_script(reimage=[[["0", "Updating"]]])
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.actions().count("reimage"), 1)
        self.assert_replaced(["0"], 1, at_clock=720)
        self.assertIn("The time limit of 720 seconds ended.", result.stdout)

    def test_only_instances_that_fail_the_reimage_are_replaced(self):
        result = self.run_script(
            capacity=2,
            start=[[["0", "Succeeded"], ["1", "Succeeded"]]],
            reimage=[[["0", "Updating"], ["1", "Updating"]], [["0", "Succeeded"], ["1", "Failed"]]],
            **{"delete-instances": [[["0", "Succeeded"]]]},
            scale=[[["0", "Succeeded"], ["2", "Succeeded"]]],
        )
        self.assertEqual(result.returncode, 0, self.output)
        self.assert_replaced(["1"], 2, at_clock=40)

    def test_replace_without_succeeded_instances_fails_with_repair_message(self):
        cases = {
            "new instance fails": {"scale": [[["1", "Failed"]]]},
            "new instance stays busy": {"scale": [[["1", "Creating"]]]},
            "new instance is missing": {"scale": [[]]},
            "delete does not end": {"delete-instances": [[["0", "Deleting"]]]},
        }
        for name, scenario in cases.items():
            with self.subTest(name):
                result = self.run_script(reimage=[[["0", "Failed"]]], **scenario)
                self.assert_repair_message(result)
                self.assertEqual(self.actions().count("delete-instances"), 1)
                self.assertLessEqual(self.clock, 20 + 600)

    def test_worst_case_ends_before_the_login_expires(self):
        # The instance becomes free at the end of the first wait, then stays stuck in the reimage,
        # and Azure does not delete it.
        result = self.run_script(
            start=[[["0", "Updating"]]] * 15 + [ONE_GOOD],
            reimage=[[["0", "Updating"]]],
            **{"delete-instances": [[["0", "Deleting"]]]},
        )
        self.assert_repair_message(result)
        self.assertIn("did not delete instances 0 in time", result.stderr)
        self.assertEqual(self.clock, 300 + 720 + 600)
        self.assertLess(self.clock, LOGIN_LIFETIME_SECONDS)

    def test_real_clock_with_small_time_limits(self):
        result = self.run_script(
            fake_clock=False,
            environment={
                "REIMAGE_IDLE_WAIT_SECONDS": "1",
                "REIMAGE_WAIT_SECONDS": "1",
                "REIMAGE_REPLACE_WAIT_SECONDS": "5",
                "REIMAGE_POLL_SECONDS": "1",
            },
            reimage=[[["0", "Updating"]]],
            **{"delete-instances": [[]]},
        )
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.actions().count("reimage"), 1)
        self.assertEqual(self.actions().count("delete-instances"), 1)
        self.assertRegex(result.stdout, r"\[\d+s\] The replace is complete\.")

    def test_missing_or_wrong_settings_stop_before_azure(self):
        cases = [
            ({"VMSS_NAME": ""}, "Set VMSS_NAME"),
            ({"RESOURCE_GROUP": ""}, "Set RESOURCE_GROUP"),
            ({"REIMAGE_WAIT_SECONDS": "12m"}, "REIMAGE_*_SECONDS"),
            ({"REIMAGE_POLL_SECONDS": "-1"}, "REIMAGE_*_SECONDS"),
        ]
        for environment, message in cases:
            with self.subTest(environment=environment):
                result = self.run_script(environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.az_calls, [])


class ReimageTerraformTests(unittest.TestCase):
    def test_terraform_runs_the_script_with_the_scale_set_values(self):
        text = (MODULE / "main.tf").read_text()
        block = re.search(r'^resource "terraform_data" "vm_linux_reimage" \{\n(.*?)^\}', text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(block)
        block = block.group(1)
        self.assertRegex(block, r'interpreter\s*=\s*\["/bin/bash"\]')
        self.assertRegex(block, r'command\s*=\s*"\$\{path\.module\}/reimage_instances\.sh"')
        self.assertRegex(block, r"VMSS_NAME\s*=\s*azurerm_linux_virtual_machine_scale_set\.vm_linux\.name\n")
        self.assertRegex(block, r"RESOURCE_GROUP\s*=\s*var\.resource_group_name\n")
        self.assertIn("azurerm_linux_virtual_machine_scale_set.vm_linux.custom_data", block)
        self.assertNotIn("az vmss reimage", text)
        self.assertTrue(SCRIPT.is_file())


if __name__ == "__main__":
    unittest.main()
