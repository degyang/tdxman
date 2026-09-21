"""Bounded fetchers: one connection per worker, never shared concurrent requests."""

import asyncio
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from time import perf_counter


def fetch_sync(items, factory, fetch, workers=1, retry_factory=None):
    if workers < 1:
        raise ValueError("workers must be positive")
    local = threading.local()
    clients = []
    clients_lock = threading.Lock()

    def run(item):
        start = perf_counter()
        try:
            if not hasattr(local, "client"):
                manager = factory()
                client = manager.__enter__()
                with clients_lock:
                    clients.append(manager)
                local.manager = manager
                local.client = client
            return item, fetch(local.client, item), None, perf_counter() - start
        except Exception as exc:
            if retry_factory is None:
                return item, None, exc, perf_counter() - start
            # A failed cached endpoint may be stale. Re-select once and retry
            # this item on a fresh, private connection.
            try:
                old = getattr(local, "manager", None)
                if old is not None:
                    with clients_lock:
                        clients.remove(old)
                    del local.manager
                    del local.client
                    old.__exit__(None, None, None)
                manager = retry_factory()
                client = manager.__enter__()
                with clients_lock:
                    clients.append(manager)
                local.manager = manager
                local.client = client
                return item, fetch(client, item), None, perf_counter() - start
            except Exception as retry_error:
                return item, None, retry_error, perf_counter() - start

    try:
        if workers == 1:
            for item in items:
                yield run(item)
        else:
            iterator = iter(items)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                pending = set()
                for _ in range(workers * 2):
                    item = next(iterator, None)
                    if item is None:
                        break
                    pending.add(executor.submit(run, item))
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        yield future.result()
                        item = next(iterator, None)
                        if item is not None:
                            pending.add(executor.submit(run, item))
    finally:
        for client in clients:
            client.__exit__(None, None, None)


async def fetch_async(
    items,
    factory,
    fetch,
    workers=1,
    retry_factory=None,
    request_timeout: float | None = 30.0,
    max_retries: int = 2,
    retry_backoff: float = 0.5,
):
    if workers < 1:
        raise ValueError("workers must be positive")
    if request_timeout is not None and request_timeout <= 0:
        raise ValueError("request_timeout must be positive")
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    if retry_backoff < 0:
        raise ValueError("retry_backoff must be non-negative")
    items = list(items)
    workers = min(workers, max(1, len(items)))
    queue = asyncio.Queue(maxsize=workers * 2)

    async def run(chunk):
        remaining = list(chunk)

        async def invoke(client, item):
            try:
                operation = fetch(client, item)
                if request_timeout is None:
                    return await operation
                return await asyncio.wait_for(operation, timeout=request_timeout)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"异步请求超时（{request_timeout:g}s）") from exc

        async def request_with_retry(client, item):
            last_error = None
            for attempt in range(max_retries + 1):
                if attempt == 0 or retry_factory is None:
                    try:
                        return await invoke(client, item)
                    except Exception as exc:  # noqa: BLE001 - retry the transport boundary
                        last_error = exc
                else:
                    if retry_backoff:
                        await asyncio.sleep(retry_backoff * (2 ** (attempt - 1)))
                    try:
                        async with retry_factory() as retry_client:
                            return await invoke(retry_client, item)
                    except Exception as exc:  # noqa: BLE001 - retry the transport boundary
                        last_error = exc
                if attempt == max_retries:
                    raise last_error  # type: ignore[misc]
            raise AssertionError("unreachable")

        try:
            async with factory() as client:
                for item in chunk:
                    start = perf_counter()
                    try:
                        result = await request_with_retry(client, item)
                        entry = (item, result, None, perf_counter() - start)
                    except Exception as exc:
                        entry = (item, None, exc, perf_counter() - start)
                    await queue.put(entry)
                    remaining.pop(0)
        except Exception as exc:
            for item in remaining:
                await queue.put((item, None, exc, 0))
        await queue.put(None)

    tasks = [asyncio.create_task(run(items[i::workers])) for i in range(workers)]
    try:
        finished = 0
        while finished < workers:
            entry = await queue.get()
            if entry is None:
                finished += 1
            else:
                yield entry
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def client_factory(client_type, workers):
    """Use the remembered endpoint; refresh selection once after a request failure."""
    refreshed = False
    refresh_lock = threading.Lock()

    def cached():
        return client_type.from_best_host(refresh=False, heartbeat_interval=0)

    def reselect():
        nonlocal refreshed
        with refresh_lock:
            if not refreshed:
                refreshed = True
                return client_type.from_best_host(heartbeat_interval=0)
        return cached()

    return cached, reselect
