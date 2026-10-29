import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "templates/shared_services/certs/scripts/letsencrypt.sh"
OPTIONS = [
    "--storage_account_name",
    "--fqdn",
    "--keyvault_name",
    "--resource_group_name",
    "--application_gateway_name",
    "--cert_name",
    "--password_name",
]


class CertificateArgumentsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.log"
        for name in ["az", "jq"]:
            stub = self.bin / name
            stub.write_text(
                '#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALL_LOG"\nexit 73\n'
                if name == "az"
                else "#!/bin/bash\ncat >/dev/null\nexit 73\n"
            )
            stub.chmod(0o755)
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "CALL_LOG": str(self.log),
        }
        self.arguments = [part for option in OPTIONS for part in (option, "test-value")]

    def run_script(self, arguments):
        self.log.unlink(missing_ok=True)
        return subprocess.run(
            ["/bin/bash", str(SCRIPT), *arguments],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def assert_argument_error(self, arguments, diagnostic):
        result = self.run_script(arguments)
        self.assertEqual(result.returncode, 1)
        self.assertIn(diagnostic, result.stderr)
        self.assertIn("Usage:", result.stderr)
        self.assertNotIn("unbound variable", result.stderr)
        self.assertNotIn("command not found", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.root / "index.html").exists())

    def test_each_option_requires_a_value(self):
        for option in [*OPTIONS, "--start_gateway"]:
            for value in ([], [""], ["--unknown"]):
                with self.subTest(option=option, value=value):
                    self.assert_argument_error([option, *value], f"Missing value for {option}")

    def test_each_required_option_must_be_supplied(self):
        for index, option in enumerate(OPTIONS):
            with self.subTest(option=option):
                arguments = self.arguments[: index * 2] + self.arguments[index * 2 + 2 :]
                self.assert_argument_error(arguments, f"Missing value for {option}")

    def test_no_arguments_are_rejected(self):
        self.assert_argument_error([], "Missing value for --storage_account_name")

    def test_unknown_options_are_rejected(self):
        self.assert_argument_error(["--unknown"], "Unexpected argument: '--unknown'")

    def test_invalid_start_gateway_is_rejected_before_azure_operations(self):
        self.assert_argument_error(
            [*self.arguments, "--start_gateway", "invalid"], "start_gateway must be true or false"
        )

    def test_complete_arguments_reach_mocked_azure_operation(self):
        for arguments in (self.arguments, [part for option in reversed(OPTIONS) for part in (option, "test-value")]):
            with self.subTest(arguments=arguments):
                result = self.run_script(arguments)
                self.assertEqual(result.returncode, 73)
                self.assertNotIn("Usage:", result.stderr)
                self.assertIn("network application-gateway start", self.log.read_text())
