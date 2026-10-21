"""Check the order of Azure Backup registration in the base workspace Terraform."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BackupRegistrationTests(unittest.TestCase):
    def test_storage_registration_waits_for_firewall_and_file_endpoint(self):
        source = (ROOT / "templates/workspaces/base/terraform/storage.tf").read_text()
        container = source.split('resource "azurerm_backup_container_storage_account" "storage_account" {', 1)[1]
        container = container.split("\nresource ", 1)[0]
        depends_on = re.search(r"depends_on\s*=\s*\[([^\]]*)\]", container)
        self.assertIsNotNone(depends_on)
        dependencies = {item.strip() for item in depends_on.group(1).split(",") if item.strip()}
        self.assertLessEqual(
            {"azurerm_storage_account_network_rules.stgrules", "azurerm_private_endpoint.stgfilepe"}, dependencies
        )
