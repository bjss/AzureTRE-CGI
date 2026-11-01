"""Mask SAS values in the httpx log lines of the E2E tests.

httpx logs each request URL at INFO level, and pytest.ini prints INFO logs to the CI log.
A blob URL with a shared access signature (SAS) puts its signature in that URL.
"""

from collections.abc import Mapping
import logging
import re

import httpx

# Query parameters of an Azure Storage SAS.
SAS_PARAMETERS = (
    "sig",
    "se",
    "st",
    "sp",
    "sv",
    "sr",
    "spr",
    "sip",
    "si",
    "ss",
    "srt",
    "sdd",
    "ses",
    "skoid",
    "sktid",
    "skt",
    "ske",
    "sks",
    "skv",
    "skdutid",
    "saoid",
    "suoid",
    "sduoid",
    "scid",
)
_SAS_VALUE = re.compile(r"(?<=[?&])(" + "|".join(SAS_PARAMETERS) + r")=[^&#\s\"']*", re.IGNORECASE)


def mask_sas(text: str) -> str:
    """Replace the value of each SAS query parameter in the text with REDACTED."""
    return _SAS_VALUE.sub(r"\1=REDACTED", text)


def _masked(value):
    # httpx gives the request URL as an httpx.URL object. Other argument types keep their value and type.
    if not isinstance(value, (str, httpx.URL)):
        return value
    text = str(value)
    masked = mask_sas(text)
    return value if masked == text else masked


class SasLogFilter(logging.Filter):
    """Mask SAS values in the message and the arguments of each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _masked(record.msg)
        if isinstance(record.args, Mapping):
            record.args = {key: _masked(value) for key, value in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(_masked(value) for value in record.args)
        return True


def mask_sas_in_httpx_logs():
    """Add the filter to the httpx logger, one time only."""
    logger = logging.getLogger("httpx")
    if not any(isinstance(item, SasLogFilter) for item in logger.filters):
        logger.addFilter(SasLogFilter())
