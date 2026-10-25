"""Check the private network settings of the Health bundle."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "templates/workspace_services/health-services/terraform"


def block(source, header):
    return source.split(header + " {", 1)[1].split("\n}", 1)[0]


class HealthBundleContractTests(unittest.TestCase):
    def test_dns_zone_group_name_follows_the_services(self):
        # Azure does not always add a DNS record for a service that joins after the endpoint.
        # A new group name makes the provider create the group again, and the endpoint stays.
        source = (BUNDLE / "main.tf").read_text()
        endpoint = block(source, 'resource "azurerm_private_endpoint" "health_services_private_endpoint"')
        group = block(endpoint, "  private_dns_zone_group")
        name = next(line for line in group.splitlines() if line.strip().startswith("name"))
        self.assertIn('var.deploy_fhir ? ["fhir"] : []', name)
        self.assertIn('var.deploy_dicom ? ["dicom"] : []', name)
        self.assertNotIn("replace_triggered_by", endpoint)

    def test_dicom_public_network_access_is_off(self):
        # The provider turns it on unless the bundle sets it.
        source = (BUNDLE / "main.tf").read_text()
        dicom = block(source, 'resource "azurerm_healthcare_dicom_service" "dicom"')
        self.assertRegex(dicom, r"\n  public_network_access_enabled\s*=\s*false\n")
