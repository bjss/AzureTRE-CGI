"""Exercise the resource processor reimage step with a mocked Azure CLI and a mocked clock."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest


MODULE = Path(__file__).resolve().parents[2] / "core/terraform/resource_processor/vmss_porter"
SCRIPT = MODULE / "reimage_instances.sh"
LOGIN_LIFETIME_SECONDS = 40 * 60
# The worst case in the header of the script, with the default values.
WORST_CASE_SECONDS = 310 + 720 + 60 + 680
HANG = "hang"

# The mocked "az" returns scripted instance states. Each action (reimage, delete-instances and scale)
# starts a new list of states, and the last item of a list repeats. The item "error" makes the read fail,
# and the item "hang" makes the read hang. The reimage ends with the exit code "reimage_exit", or it hangs
# with "hang". The actions in the list "hang" (show, delete-instances or scale) hang.
# A call that hangs does not end. Under the mocked "timeout" it returns 200 at once, and under the real
# "timeout" it waits until "timeout" stops it. Without "timeout", it ends with an error only when the
# Azure CLI login expires. With "slow", each call other than the reimage uses all of its time limit.
MOCK_AZ = r"""
import json
import os
from pathlib import Path
import sys
import time

HANG = "hang"
root = Path(os.environ["MOCK_ROOT"])
config = json.loads((root / "config.json").read_text())
state_file = root / "state.json"
state = json.loads(state_file.read_text()) if state_file.exists() else {"phase": "start", "index": 0}
args = sys.argv[1:]
limit = os.environ.get("MOCK_TIMEOUT")
clock_file = root / "clock"
with (root / "calls.jsonl").open("a") as log:
    entry = {"clock": int(clock_file.read_text()), "args": args, "timeout": limit}
    log.write(json.dumps(entry) + "\n")


def option(name):
    return args[args.index(name) + 1] if name in args else None


def finish(code=0, output=""):
    state_file.write_text(json.dumps(state))
    if output:
        print(output, file=sys.stderr if code else sys.stdout)
    sys.exit(code)


def move_clock(seconds):
    clock_file.write_text(str(int(clock_file.read_text()) + seconds))


def hang():
    if limit is not None:
        finish(200)
    if config["fake_clock"]:
        move_clock(config["login_lifetime"])
        finish(1, "ERROR: AADSTS700024: Client assertion is not within its valid time range.")
    state_file.write_text(json.dumps(state))
    time.sleep(30)
    finish(200)


if option("--name") == "vmss-mock" and option("--resource-group") == "rg-mock":
    action = args[1]
    tsv = option("--output") == "tsv"
    if action != "reimage":
        if action in config["hang"]:
            hang()
        if config["slow"]:
            if limit is None:
                hang()
            move_clock(int(limit))
    if action == "list-instances" and tsv and option("--query") == "[].[instanceId, provisioningState]":
        states = config[state["phase"]]
        current = states[min(state["index"], len(states) - 1)]
        state["index"] += 1
        if current == HANG:
            hang()
        if current == "error":
            finish(1, "ERROR: AADSTS700024: Client assertion is not within its valid time range.")
        finish(output="\n".join(instance_id + "\t" + value for instance_id, value in current))
    if action == "show" and tsv and option("--query") == "sku.capacity":
        finish(output=str(config["capacity"]))
    if action == "reimage" and "--no-wait" not in args:
        state["phase"], state["index"] = action, 0
        if config["reimage_exit"] == HANG:
            hang()
        finish(config["reimage_exit"], "ERROR: (VMStartTimedOut) mocked reimage error" if config["reimage_exit"] else "")
    if action in ("delete-instances", "scale") and "--no-wait" in args:
        state["phase"], state["index"] = action, 0
        finish()
finish(99, "Unexpected mocked command: " + repr(args))
"""

# The mocked "sleep" moves a clock forward, and the mocked "date" prints this clock.
# So the tests use the default time limits, and they do not wait.
MOCK_SLEEP = '#!/bin/bash\necho $(($(<"$MOCK_ROOT/clock") + $1)) >"$MOCK_ROOT/clock"\n'
MOCK_DATE = '#!/bin/bash\n[ "$*" = "+%s" ] || exit 99\necho $((1800000000 + $(<"$MOCK_ROOT/clock")))\n'
# The mocked "timeout" runs the command. If the mocked "az" hangs (exit code 200), it moves the clock
# forward by the time limit and returns 124, as the real "timeout" does.
MOCK_TIMEOUT = """#!/bin/bash
limit=$1
shift
status=0
MOCK_TIMEOUT=$limit "$@" || status=$?
if [ "$status" -eq 200 ]; then
  echo $(($(<"$MOCK_ROOT/clock") + limit)) >"$MOCK_ROOT/clock"
  exit 124
fi
exit "$status"
"""

ONE_GOOD = [["0", "Succeeded"]]


class ReimageInstancesTests(unittest.TestCase):
    def run_script(self, fake_clock=True, environment=None, **scenario):
        config = {
            "capacity": 1,
            "start": [ONE_GOOD],
            "reimage_exit": 0,
            "reimage": [ONE_GOOD],
            "delete-instances": [[["0", "Deleting"]], []],
            "scale": [[["1", "Creating"]], [["1", "Succeeded"]]],
            "hang": [],
            "slow": False,
            **scenario,
            "fake_clock": fake_clock,
            "login_lifetime": LOGIN_LIFETIME_SECONDS,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(json.dumps(config))
            (root / "clock").write_text("0")
            mocks = {"az": f"#!{sys.executable} -S\n" + MOCK_AZ}
            if fake_clock:
                mocks.update(sleep=MOCK_SLEEP, date=MOCK_DATE, timeout=MOCK_TIMEOUT)
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
        self.az_calls = [(call["clock"], call["args"], call["timeout"]) for call in calls]
        self.output = result.stdout + result.stderr
        return result

    def actions(self):
        return [args[1] for _, args, _ in self.az_calls]

    def read_limits(self):
        return [limit for _, args, limit in self.az_calls if args[1] == "list-instances"]

    def action_call(self, action):
        matches = [call for call in self.az_calls if call[1][1] == action]
        self.assertEqual(len(matches), 1, self.output)
        return matches[0]

    def assert_replaced(self, instance_ids, capacity, at_clock):
        clock, args, _ = self.action_call("delete-instances")
        self.assertEqual(clock, at_clock)
        self.assertEqual(args[args.index("--instance-ids") + 1 : args.index("--no-wait")], instance_ids)
        _, args, _ = self.action_call("scale")
        self.assertEqual(args[args.index("--new-capacity") + 1], str(capacity))

    def assert_repair_message(self, result):
        self.assertEqual(result.returncode, 1, self.output)
        self.assertIn("ERROR:", result.stderr)
        self.assertIn("scale set vmss-mock in resource group rg-mock", result.stderr)
        self.assertIn("docs/troubleshooting-faq/troubleshooting-rp.md", result.stderr)

    def test_normal_reimage_waits_for_azure_and_does_not_replace(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.actions(), ["list-instances", "reimage", "list-instances"])
        _, args, limit = self.action_call("reimage")
        self.assertNotIn("--no-wait", args)
        self.assertEqual(limit, "720")
        self.assertIn("The reimage is complete.", result.stdout)
        self.assertEqual(self.clock, 0)

    def test_busy_instance_before_reimage_gets_time_to_finish(self):
        result = self.run_script(start=[[["0", "Updating"]]] * 3 + [ONE_GOOD])
        self.assertEqual(result.returncode, 0, self.output)
        clock, _, _ = self.action_call("reimage")
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

    def test_instance_not_succeeded_after_reimage_is_replaced(self):
        cases = [
            (HANG, "Updating", 720, "did not end in the time limit of 720 seconds"),
            (1, "Failed", 0, "ended with exit code 1"),
            (0, "Updating", 0, "ended with exit code 0"),
        ]
        for reimage_exit, state, at_clock, message in cases:
            with self.subTest(reimage_exit=reimage_exit, state=state):
                result = self.run_script(reimage_exit=reimage_exit, reimage=[[["0", state]]])
                self.assertEqual(result.returncode, 0, self.output)
                self.assertEqual(self.actions().count("reimage"), 1)
                self.assert_replaced(["0"], 1, at_clock=at_clock)
                self.assertIn(message, result.stdout)
                self.assertIn("The replace is complete.", result.stdout)

    def test_only_instances_that_fail_the_reimage_are_replaced(self):
        result = self.run_script(
            capacity=2,
            start=[[["0", "Succeeded"], ["1", "Succeeded"]]],
            reimage_exit=1,
            reimage=[[["0", "Succeeded"], ["1", "Failed"]]],
            **{"delete-instances": [[["0", "Succeeded"]]]},
            scale=[[["0", "Succeeded"], ["2", "Succeeded"]]],
        )
        self.assertEqual(result.returncode, 0, self.output)
        self.assert_replaced(["1"], 2, at_clock=0)

    def test_reimage_error_with_all_instances_succeeded_fails_without_replace(self):
        cases = [
            (1, "The reimage ended with exit code 1."),
            (HANG, "The reimage did not end in the time limit of 720 seconds."),
        ]
        for reimage_exit, message in cases:
            with self.subTest(reimage_exit=reimage_exit):
                result = self.run_script(reimage_exit=reimage_exit)
                self.assert_repair_message(result)
                self.assertIn(message, result.stderr)
                self.assertIn("does not replace them", result.stderr)
                self.assertEqual(self.actions(), ["list-instances", "reimage", "list-instances"])

    def test_read_failure_after_reimage_error_stops_at_once(self):
        for reimage_exit in (1, HANG):
            with self.subTest(reimage_exit=reimage_exit):
                result = self.run_script(reimage_exit=reimage_exit, reimage=["error"])
                self.assert_repair_message(result)
                self.assertIn("Could not read the instances of the scale set.", result.stderr)
                self.assertEqual(self.actions(), ["list-instances", "reimage", "list-instances"])

    def test_replace_without_succeeded_instances_fails_with_repair_message(self):
        cases = {
            "new instance fails": {"scale": [[["1", "Failed"]]]},
            "new instance stays busy": {"scale": [[["1", "Creating"]]]},
            "new instance is missing": {"scale": [[]]},
            "delete does not end": {"delete-instances": [[["0", "Deleting"]]]},
        }
        for name, scenario in cases.items():
            with self.subTest(name):
                result = self.run_script(reimage_exit=1, reimage=[[["0", "Failed"]]], **scenario)
                self.assert_repair_message(result)
                self.assertIn("ended with exit code 1", result.stdout)
                self.assertEqual(self.actions().count("delete-instances"), 1)
                self.assertLessEqual(self.clock, 600)

    def test_worst_case_ends_before_the_login_expires(self):
        # The instance becomes free at the end of the first wait. Then the reimage does not end,
        # the instance stays busy, and Azure does not delete it.
        result = self.run_script(
            start=[[["0", "Updating"]]] * 15 + [ONE_GOOD],
            reimage_exit=HANG,
            reimage=[[["0", "Updating"]]],
            **{"delete-instances": [[["0", "Deleting"]]]},
        )
        self.assert_repair_message(result)
        self.assertIn("did not delete instances 0 in time", result.stderr)
        self.assertEqual(self.clock, 300 + 720 + 600)
        self.assertLess(self.clock, LOGIN_LIFETIME_SECONDS)

    def test_worst_case_with_slow_azure_cli_calls_ends_before_the_login_expires(self):
        # Each call other than the reimage uses all of its time limit. The instance becomes free at the
        # last read of the first wait, Azure deletes the instance at the last read of the delete wait,
        # and the new instance stays busy.
        result = self.run_script(
            slow=True,
            start=[[["0", "Updating"]]] * 3 + [ONE_GOOD],
            reimage_exit=HANG,
            reimage=[[["0", "Updating"]]],
            **{"delete-instances": [[["0", "Deleting"]]] * 6 + [[]]},
            scale=[[["1", "Creating"]]],
        )
        self.assert_repair_message(result)
        self.assertIn("After the replace, not all instances are in the state Succeeded.", result.stderr)
        # 300 for the first wait, 720 for the reimage, 60 for the read after the reimage, 600 for the
        # replace, 10 for the last read of the delete wait, 60 for the scale call and 10 for the last
        # read. The first wait ends at its time limit, so this case is 10 seconds less than the worst case.
        self.assertEqual(self.clock, 300 + 720 + 60 + 600 + 10 + 60 + 10)
        self.assertEqual(self.read_limits()[-2:], ["10", "10"])
        self.assertLessEqual(self.clock, WORST_CASE_SECONDS)
        self.assertLess(WORST_CASE_SECONDS, LOGIN_LIFETIME_SECONDS)

    def test_each_azure_cli_call_has_a_time_limit(self):
        result = self.run_script(reimage_exit=1, reimage=[[["0", "Failed"]]])
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(
            self.actions(),
            ["list-instances", "reimage", "list-instances", "show", "delete-instances"]
            + ["list-instances"] * 2
            + ["scale"]
            + ["list-instances"] * 2,
        )
        for _, args, limit in self.az_calls:
            with self.subTest(action=args[1]):
                self.assertEqual(limit, "720" if args[1] == "reimage" else "60")

    def test_read_that_stalls_in_a_wait_is_tried_again(self):
        busy = [["0", "Updating"]]
        message = (
            "Could not read the instances of the scale set. The time limit of 60 seconds for the Azure CLI call ended."
        )
        with self.subTest("the instance becomes free"):
            result = self.run_script(start=[busy, HANG, ONE_GOOD])
            self.assertEqual(result.returncode, 0, self.output)
            self.assertIn(message, result.stdout)
            # The stalled read starts at 20 and stops at 80. The next read starts at 100.
            clock, _, _ = self.action_call("reimage")
            self.assertEqual(clock, 100)
            self.assertIn("The reimage is complete.", result.stdout)
        with self.subTest("the instance stays busy until the end of the wait"):
            result = self.run_script(start=[busy, HANG, busy])
            self.assertEqual(result.returncode, 0, self.output)
            self.assertIn(message, result.stdout)
            self.assertNotIn("reimage", self.actions())
            self.assert_replaced(["0"], 1, at_clock=300)

    def test_wait_stops_the_script_when_its_last_read_stalls(self):
        busy = [["0", "Updating"]]
        cases = [
            # The reads start at 0, 20, 100, 180 and 260. The last read gets the rest of the wait.
            ("the rest of the wait", [busy, HANG], "40", 300),
            # The reads start at 0, 20, ..., 300. The last read gets at least 10 seconds.
            ("at least 10 seconds", [busy] * 15 + [HANG], "10", 310),
        ]
        for name, start, last_limit, at_clock in cases:
            with self.subTest(name):
                result = self.run_script(start=start)
                self.assert_repair_message(result)
                self.assertIn(f"The time limit of {last_limit} seconds for the Azure CLI call ended.", result.stdout)
                self.assertIn("The time limit of 300 seconds ended.", result.stdout)
                self.assertIn("The last read of the instances failed", result.stderr)
                self.assertEqual(self.read_limits()[-1], last_limit)
                self.assertEqual(set(self.actions()), {"list-instances"})
                self.assertEqual(self.clock, at_clock)

    def test_stalled_call_in_a_replace_stops_the_script(self):
        cases = [
            ("show", "Could not read the capacity of the scale set.", 60),
            ("delete-instances", "The delete of instances 0 did not start.", 60),
            # The delete wait reads at 0 and at 20. Then the scale starts.
            ("scale", "The scale of the scale set did not start.", 80),
        ]
        for action, message, at_clock in cases:
            with self.subTest(action=action):
                result = self.run_script(reimage_exit=1, reimage=[[["0", "Failed"]]], hang=[action])
                self.assert_repair_message(result)
                self.assertIn(f"{message} The time limit of 60 seconds for the Azure CLI call ended.", result.stderr)
                self.assertEqual(self.actions()[-1], action)
                self.assertEqual(self.clock, at_clock)

    def test_stalled_read_after_the_reimage_stops_the_script(self):
        result = self.run_script(reimage=[HANG])
        self.assert_repair_message(result)
        self.assertIn(
            "Could not read the instances of the scale set. The time limit of 60 seconds for the Azure CLI call ended.",
            result.stderr,
        )
        self.assertEqual(self.actions(), ["list-instances", "reimage", "list-instances"])
        self.assertEqual(self.clock, 60)

    def test_real_timeout_stops_a_reimage_that_does_not_end(self):
        started = time.monotonic()
        result = self.run_script(
            fake_clock=False,
            environment={
                "REIMAGE_IDLE_WAIT_SECONDS": "1",
                "REIMAGE_WAIT_SECONDS": "1",
                "REIMAGE_REPLACE_WAIT_SECONDS": "5",
                "REIMAGE_POLL_SECONDS": "1",
            },
            reimage_exit=HANG,
            reimage=[[["0", "Updating"]]],
            **{"delete-instances": [[]]},
        )
        self.assertEqual(result.returncode, 0, self.output)
        self.assertLess(time.monotonic() - started, 20)
        self.assertIn("did not end in the time limit of 1 seconds", result.stdout)
        self.assertEqual(self.actions().count("delete-instances"), 1)
        self.assertRegex(result.stdout, r"\[\d+s\] The replace is complete\.")

    def test_missing_or_wrong_settings_stop_before_azure(self):
        cases = [
            ({"VMSS_NAME": ""}, "Set VMSS_NAME"),
            ({"RESOURCE_GROUP": ""}, "Set RESOURCE_GROUP"),
            ({"REIMAGE_WAIT_SECONDS": "12m"}, "REIMAGE_*_SECONDS"),
            ({"REIMAGE_POLL_SECONDS": "-1"}, "REIMAGE_*_SECONDS"),
            ({"REIMAGE_CALL_SECONDS": "1m"}, "REIMAGE_*_SECONDS"),
            ({"REIMAGE_CALL_SECONDS": "0"}, '"timeout 0" has no time limit'),
            ({"REIMAGE_WAIT_SECONDS": "0"}, '"timeout 0" has no time limit'),
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
