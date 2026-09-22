"""Lazy serialized connections with bounded reconnects; no background workers."""

from __future__ import annotations

import threading

from tdxman.exceptions import TdxConnectionError

from .contracts import ProviderError
from .diagnostics import check_budget


class ManagedClient:
    """Reuse one connection under a lock and release it on every failed attempt."""

    def __init__(self, factory, timeout=10, retries=2, backoff=0.1):
        self._factory = factory
        self._timeout = timeout
        self._retries = retries
        self._backoff = backoff
        self._lock = threading.RLock()
        self._stopped = threading.Event()
        self._client = None

    def _disconnect(self):
        client, self._client = self._client, None
        if client is not None:
            client.close()

    def close(self):
        self._stopped.set()
        with self._lock:
            self._disconnect()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            with self._lock:
                for attempt in range(self._retries + 1):
                    check_budget()
                    if self._stopped.is_set():
                        raise ProviderError("PROVIDER_CLOSED", "Provider is closed")
                    try:
                        if self._client is None:
                            self._client = self._factory(
                                timeout=self._timeout, auto_reconnect=False,
                                heartbeat_interval=0,
                            )
                            self._client.connect()
                        return getattr(self._client, name)(*args, **kwargs)
                    except (TdxConnectionError, OSError) as exc:
                        self._disconnect()
                        if attempt == self._retries:
                            raise ProviderError(
                                "UPSTREAM_UNAVAILABLE", "tdxman request retries exhausted"
                            ) from exc
                        self._stopped.wait(self._backoff * 2**attempt)

        return call
