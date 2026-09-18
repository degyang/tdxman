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


async def fetch_async(items, factory, fetch, workers=1, retry_factory=None):
    if workers < 1:
        raise ValueError("workers must be positive")
    items = list(items)
    workers = min(workers, max(1, len(items)))
    queue = asyncio.Queue(maxsize=workers * 2)

    async def run(chunk):
        remaining = list(chunk)
        try:
            async with factory() as client:
                for item in chunk:
                    start = perf_counter()
                    try:
                        result = await fetch(client, item)
                        entry = (item, result, None, perf_counter() - start)
                    except Exception as exc:
                        if retry_factory is None:
                            entry = (item, None, exc, perf_counter() - start)
                        else:
                            try:
                                async with retry_factory() as retry_client:
                                    result = await fetch(retry_client, item)
                                entry = (item, result, None, perf_counter() - start)
                            except Exception as retry_error:
                                entry = (item, None, retry_error, perf_counter() - start)
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
