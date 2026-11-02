"""Mask SAS values in the httpx log lines of the E2E tests."""

import logging
import unittest

from httpx import AsyncClient, MockTransport, Response

# The E2E conftest adds the filter to the httpx logger when pytest loads it.
from e2e_tests import conftest  # noqa: F401
from e2e_tests.sas_log_filter import SAS_PARAMETERS, SasLogFilter, mask_sas, mask_sas_in_httpx_logs


SIGNATURE = "c2lnbmF0dXJlLXZhbHVl%3D"
OBJECT_ID = "6e0b7a59-3d1e-4c43-9a5b-0f0d6f3c1a11"
SAS = f"sv=2025-01-05&se=2026-11-02T10%3A00%3A00Z&sr=b&sp=r&skoid={OBJECT_ID}&sig={SIGNATURE}"
BLOB = "https://stexample.blob.core.windows.net/container/file.txt"


class SasLogFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_e2e_tests_mask_the_sas_in_the_httpx_request_line(self):
        with self.assertLogs("httpx", level="INFO") as logs:
            async with AsyncClient(transport=MockTransport(lambda request: Response(200))) as client:
                await client.get(f"{BLOB}?{SAS}")
        log = "\n".join(logs.output)
        self.assertIn(
            f'HTTP Request: GET {BLOB}?sv=REDACTED&se=REDACTED&sr=REDACTED&sp=REDACTED&skoid=REDACTED&sig=REDACTED "HTTP/1.1 200 OK"',
            log,
        )
        for value in (SIGNATURE, OBJECT_ID):
            self.assertNotIn(value, log)
            self.assertNotIn(value, repr(logs.records[0].args))

    def test_every_sas_parameter_is_masked_and_other_parameters_are_kept(self):
        query = "&".join(f"{name}=value{index}" for index, name in enumerate(SAS_PARAMETERS))
        masked = mask_sas(f"{BLOB}?restype=container&{query}&comp=list&signature=kept&SIG=upper")
        self.assertEqual(
            masked,
            f"{BLOB}?restype=container&"
            + "&".join(f"{name}=REDACTED" for name in SAS_PARAMETERS)
            + "&comp=list&signature=kept&SIG=REDACTED",
        )

    def test_message_and_mapping_arguments_are_masked(self):
        for message, args in ((f"Read {BLOB}?{SAS}", ()), ("Read %(url)s", ({"url": f"{BLOB}?{SAS}"},))):
            with self.subTest(message=message):
                record = logging.LogRecord("httpx", logging.INFO, __file__, 1, message, args, None)
                self.assertTrue(SasLogFilter().filter(record))
                self.assertNotIn(SIGNATURE, record.getMessage())
                self.assertIn("sig=REDACTED", record.getMessage())

    def test_the_filter_is_added_one_time(self):
        mask_sas_in_httpx_logs()
        filters = [item for item in logging.getLogger("httpx").filters if isinstance(item, SasLogFilter)]
        self.assertEqual(len(filters), 1)


if __name__ == "__main__":
    unittest.main()
