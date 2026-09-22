"""Per-request budgets and structured diagnostics, isolated by context."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from inspect import isgeneratorfunction

from .contracts import ProviderError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Request:
    request_id: str
    deadline: float


_request = ContextVar("tdxman_provider_request", default=None)


def check_budget():
    request = _request.get()
    if request is not None and time.monotonic() >= request.deadline:
        raise ProviderError("DEADLINE_EXCEEDED", "Provider request deadline exceeded")


@contextmanager
def _scope(provider, method):
    if _request.get() is not None:
        check_budget()
        yield
        return
    started = time.monotonic()
    request = Request(uuid.uuid4().hex, started + provider._config.request_timeout)
    token = _request.set(request)
    status = "ok"
    try:
        yield
    except BaseException as exc:
        status = getattr(exc, "code", type(exc).__name__)
        raise
    finally:
        logger.info("provider_request id=%s method=%s status=%s elapsed=%.3f",
                    request.request_id, method, status, time.monotonic() - started)
        _request.reset(token)


def bounded_request(method):
    if isgeneratorfunction(method):
        @wraps(method)
        def iterate(self, *args, **kwargs):
            with _scope(self, method.__name__):
                yield from method(self, *args, **kwargs)
        return iterate

    @wraps(method)
    def call(self, *args, **kwargs):
        with _scope(self, method.__name__):
            return method(self, *args, **kwargs)
    return call
