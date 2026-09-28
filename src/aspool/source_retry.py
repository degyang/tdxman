"""Bounded retries for source IO; database writes are never retried here."""

import time

from tdxman.exceptions import TdxError


class EmptySourceResponse(OSError):
    """The source returned no usable records for the requested keys."""


def read_with_retry(read, *, retries=2, delay=1.0, on_retry=None):
    if not 0 <= retries <= 5 or not 0 <= delay <= 30:
        raise ValueError("Expected retries 0..5 and delay 0..30 seconds")
    for attempt in range(retries + 1):
        try:
            return read()
        except (OSError, TdxError) as exc:
            if attempt == retries:
                raise
            if on_retry is not None:
                on_retry(attempt + 1, exc)
            time.sleep(min(delay * 2**attempt, 30))
