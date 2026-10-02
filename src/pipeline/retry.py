"""Network retries — two rules, because repeating a paid request can cost money.

- `submit_retry`: a PAID, non-idempotent request (Claude generation, fal job submit). Retried only
  when the provider surely did not do the work: 5xx or the request never arrived (connect error).
  A read timeout after sending is NOT retried — the provider may have run and billed it.
- `read_retry`: idempotent reads (job status, result, file download): 5xx, timeouts and dropped
  connections are retried, which is safe and free.

4xx (including 429) is never retried. SDK-level retries are off (max_retries=0).
"""

from __future__ import annotations

import anthropic
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout)


def is_retryable_read(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    return isinstance(exc, httpx.TimeoutException | httpx.TransportError)


def is_retryable_submit(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500
    if isinstance(exc, anthropic.APITimeoutError):
        return False  # the server may have run it
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    return isinstance(exc, _NOT_SENT)


def _policy(predicate):
    return retry(
        retry=retry_if_exception(predicate),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=8),
        reraise=True,
    )


submit_retry = _policy(is_retryable_submit)
read_retry = _policy(is_retryable_read)
