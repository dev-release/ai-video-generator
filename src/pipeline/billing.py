"""Did the provider bill a failed call? One rule for all models.

The cost reserve is refunded when the request surely did not run (4xx on submit, missing key,
request never arrived) and kept when the outcome is unknown (timeout after sending, failed
job). When in doubt, count it as spent: better to under-use the budget than overspend.
"""

from __future__ import annotations

import anthropic
import httpx

from pipeline.voices import ConfigError


def not_billed(exc: BaseException) -> bool:
    explicit = getattr(exc, "billed", None)
    if explicit is not None:
        return explicit is False
    if isinstance(exc, ConfigError):
        return True
    if isinstance(exc, anthropic.APITimeoutError):
        return False
    if isinstance(exc, anthropic.APIStatusError | anthropic.APIConnectionError):
        return True  # rejected (4xx/429/5xx) or not sent
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # Generation starts with a POST; an error there means it did not run. An error on a
        # read (GET) may come after the job already ran and billed.
        return exc.request.method == "POST"
    return False
