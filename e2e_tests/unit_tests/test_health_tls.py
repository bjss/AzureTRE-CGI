import unittest
from unittest.mock import AsyncMock, Mock, patch

from e2e_tests import test_provisioned_health_api as health


class HealthTlsTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_uses_configured_tls_verification(self):
        for verify in (True, False):
            with self.subTest(verify=verify):
                response = Mock(status_code=200)
                response.json.return_value = {
                    "services": [
                        {"service": "Cosmos DB", "status": "OK", "message": ""},
                        {"service": "Service Bus", "status": "OK", "message": ""},
                        {"service": "Resource Processor", "status": "OK", "message": ""},
                    ]
                }
                client = AsyncMock()
                client.get.return_value = response
                with (
                    patch.object(health, "AsyncClient") as factory,
                    patch.object(health.config, "TRE_URL", "https://tre.example.test"),
                ):
                    factory.return_value.__aenter__.return_value = client

                    await health.test_health(verify=verify)

                    factory.assert_called_once_with(verify=verify)
                    client.get.assert_awaited_once_with(
                        f"https://tre.example.test{health.strings.API_HEALTH}", timeout=30
                    )
