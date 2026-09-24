"""Verify and measure bounded published-event reads in an isolated process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aspool import DataPool

GIB = 1024**3


def process_sample(path: Path) -> dict:
    values = {}
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith(('VmRSS:', 'VmSwap:')):
            key, value = line.split(':', 1)
            values[key] = int(value.split()[0]) * 1024
    cache_size = spill_size = 0
    for item in path.rglob('*'):
        try:
            if item.is_file():
                size = item.stat().st_size
                if item.relative_to(path).parts[0] == 'cache':
                    cache_size += size
                else:
                    spill_size += size
        except FileNotFoundError:
            # A completed query may remove its spill file between enumeration and stat.
            continue
    return {
        'rss': values.get('VmRSS', 0), 'swap': values.get('VmSwap', 0),
        'cache_bytes': cache_size, 'spill_bytes': spill_size,
        'fds': len(list(Path('/proc/self/fd').iterdir())),
        'threads': len(list(Path('/proc/self/task').iterdir())),
    }


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def source_manifest(pool, start, end):
    coverage = pool.read_limit_coverage(start=start, end=end)
    stale = pool.read_limit_staleness(start=start, end=end)
    return {
        'coverage': json.loads(coverage.to_json(orient='records', date_format='iso')),
        'staleness': json.loads(stale.to_json(orient='records', date_format='iso')),
    }


def code_manifest():
    import duckdb
    import pandas
    import pyarrow

    root = Path(__file__).resolve().parents[1]
    files = [root / 'pyproject.toml', *sorted((root / 'src').rglob('*.py'))]
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode() + b'\0' + path.read_bytes())
    return {
        'source_sha256': digest.hexdigest(),
        'git_head': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
        ).strip(),
        'python': platform.python_version(), 'duckdb': duckdb.__version__,
        'pandas': pandas.__version__, 'pyarrow': pyarrow.__version__,
        'openblas_threads': os.environ.get('OPENBLAS_NUM_THREADS'),
        'omp_threads': os.environ.get('OMP_NUM_THREADS'),
    }


def verify_amount(pool, frame):
    import pandas as pd

    checked = 0
    for day, rows in frame.head(10).groupby('trade_date'):
        daily = pool.read_research_daily(
            symbols=rows.symbol.tolist(), start=day, end=day,
            fields=['symbol', 'date', 'amount'],
        ).rename(columns={'date': 'trade_date', 'amount': 'expected_amount'})
        joined = rows.merge(daily, on=['symbol', 'trade_date'], how='left', validate='one_to_one')
        pd.testing.assert_series_equal(
            joined.amount, joined.expected_amount, check_names=False, check_dtype=False,
            check_exact=True,
        )
        checked += len(joined)
    return checked


def consume(pool, args, cache=None, verify=False):
    import pandas as pd

    filters = {} if args.all_events else {'close_limit_up': True, 'min_consecutive_up': 1}
    stats = dict(rows=0, batches=0, max_batch_rows=0, null_amounts=0,
                 compared_rows=0, compared_windows=0, amount_checked=0)
    start, end = pd.Timestamp(args.start).date(), pd.Timestamp(args.end).date()
    window_count = (end - start).days // args.batch_days + 1
    with pool.iter_limit_events_with_amount(
        start=start, end=end, batch_days=args.batch_days, max_rows=args.max_rows,
        temp_directory=cache.parent if cache else None, **filters,
    ) as batches:
        for window in range(window_count):
            lo = start + timedelta(days=window * args.batch_days)
            hi = min(end, lo + timedelta(days=args.batch_days - 1))
            if verify:
                expected = pool.read_limit_events(start=lo, end=hi)
                if not args.all_events:
                    expected = expected[
                        expected.close_limit_up.eq(True) & expected.consecutive_up.ge(1)
                    ]
                expected = expected.reset_index(drop=True)
                stats['compared_windows'] += 1
                if expected.empty:
                    continue
            try:
                frame = next(batches)
            except StopIteration:
                if verify:
                    raise AssertionError(f'Missing expected events in {lo}..{hi}') from None
                break
            if verify:
                pd.testing.assert_frame_equal(
                    frame[list(expected)].reset_index(drop=True), expected,
                    check_dtype=False, check_exact=True,
                )
                stats['compared_rows'] += len(frame)
                if window in {0, window_count // 2, window_count - 1}:
                    stats['amount_checked'] += verify_amount(pool, frame)
                del expected
            stats['rows'] += len(frame)
            stats['batches'] += 1
            stats['max_batch_rows'] = max(stats['max_batch_rows'], len(frame))
            stats['null_amounts'] += int(frame.amount.isna().sum())
            if cache:
                frame.to_parquet(cache / f"part-{stats['batches']:05d}.parquet", index=False)
            del frame
        try:
            next(batches)
        except StopIteration:
            pass
        else:
            raise AssertionError('Unexpected extra events after complete window')
        assert batches.completed
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.home() / '.aspool')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--batch-days', type=int, default=7)
    parser.add_argument('--max-rows', type=int, default=25_000)
    parser.add_argument('--warmup', action='store_true')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--all-events', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (5 * GIB, 5 * GIB))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pool = DataPool(args.root)
    sources = source_manifest(pool, args.start, args.end)
    args.output.with_suffix('.sources.json').write_text(json.dumps(sources, indent=2) + '\n')
    report = {
        'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
        'code': code_manifest(), 'root': str(pool.root),
        'start': args.start, 'end': args.end, 'sessions': len(sources['coverage']),
        'source_sha256': fingerprint(sources), 'verify': args.verify, 'all_events': args.all_events,
        'settings': dict(batch_days=args.batch_days, max_rows=args.max_rows,
                         memory_limit='512MB', threads=2, sample_seconds=0.02,
                         virtual_memory_limit=5 * GIB, rss_limit=2 * GIB),
        'warmup': args.warmup, 'runs': [],
    }
    try:
        if args.warmup:
            consume(pool, args)
        for _ in range(args.repeat):
            with tempfile.TemporaryDirectory(prefix='regime-benchmark-') as base:
                base = Path(base)
                cache = base / 'cache'
                cache.mkdir()
                baseline = process_sample(base)
                peak = dict(baseline)
                stop = threading.Event()
                monitor_errors = []

                def sample_loop():
                    try:
                        while not stop.wait(0.02):
                            sample = process_sample(base)
                            for key, value in sample.items():
                                peak[key] = max(peak[key], value)
                            if sample['rss'] > 2 * GIB:
                                os.write(2, b'Regime benchmark exceeded 2 GiB RSS\n')
                                os._exit(75)
                    except Exception as exc:
                        monitor_errors.append(str(exc))

                sampler = threading.Thread(target=sample_loop, daemon=True)
                sampler.start()
                started = time.monotonic()
                try:
                    stats = consume(pool, args, cache, args.verify)
                finally:
                    stop.set()
                    sampler.join()
                ended = process_sample(base)
                highwater = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
                report['runs'].append({
                    **stats, 'seconds': round(time.monotonic() - started, 3),
                    'process_highwater_rss': highwater,
                    'baseline': baseline, 'peak': peak, 'ended': ended,
                    'monitor_errors': monitor_errors,
                })
                assert not monitor_errors, monitor_errors
                assert highwater <= 2 * GIB, highwater
                assert ended['fds'] == baseline['fds'], 'File descriptor growth'
                assert ended['threads'] == baseline['threads'], 'Thread growth'
        final_source = fingerprint(source_manifest(pool, args.start, args.end))
        assert final_source == report['source_sha256'], (
            'Source changed during acceptance run'
        )
        assert code_manifest()['source_sha256'] == report['code']['source_sha256'], (
            'Package source changed during acceptance run'
        )
        report['status'] = 'passed'
    except BaseException as exc:
        report['status'] = 'failed'
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({key: value for key, value in report.items() if key != 'code'}))


if __name__ == '__main__':
    main()
