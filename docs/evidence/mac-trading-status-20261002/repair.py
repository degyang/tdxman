"""Repair six verified MAC historical records through the canonical daily writer."""
import json
import shutil
import sqlite3
from pathlib import Path

from aspool import DataPool
from aspool.pool import pool_lock
from aspool.sqlite_stock_store import stock_connection
from aspool.sqlite_daily_update import apply_daily_changes
from aspool.sqlite_publication import assert_published

ROOT = Path('/mnt/d/Workstation/Services/tdxman/data').resolve()
RECOVERY = Path('/home/ubuntu/tdxman-kline-status-recovery-20261002')
EVIDENCE = Path('/home/ubuntu/tdxman-kline-status-execution-20261002')
EVIDENCE.mkdir(exist_ok=True)
backup = json.loads((RECOVERY / 'recovery.json').read_text())
assert len(backup['files']) == 3 and len(backup['six_before_rows']) == 6
proof = json.loads(Path('/tmp/tdxman-mac-bj-history-proof-20261002.json').read_text())
bars, facts = [], []
for call in proof['calls']:
    symbol = call['symbol']
    for row in call['selected_rows']:
        day = row['datetime'][:10]
        assert day in {'2026-09-22', '2026-09-23', '2026-09-24'}
        op, hi, lo, cl = [row[key] for key in ('open', 'high', 'low', 'close')]
        assert 0 < lo <= min(op, cl) <= max(op, cl) <= hi and row['vol'] > 0 and row['amount'] > 0
        key = {'symbol': symbol, 'trade_date': day}
        bars.append({**key, **{k: row[k] for k in ('open', 'high', 'low', 'close', 'amount')},
                     'volume': row['vol'], 'ohlcv_source': 'tdxman:kline'})
        facts.append({**key, 'trading_status': 'TRADING', 'trading_status_source': 'tdxman:kline'})
assert len(bars) == 6
(EVIDENCE / 'mac-source-observation.json').write_text(json.dumps(proof, indent=2) + '\n')
with pool_lock(ROOT, write=True):
    assert_published(ROOT)
    # Include the layout/catalog metadata so the recovery databases are publicly readable.
    if (ROOT / 'catalog.duckdb.wal').exists():
        raise RuntimeError('Close catalog WAL before recovery validation')
    shutil.copy2(ROOT / 'catalog.duckdb', RECOVERY / 'catalog.duckdb')
    restored = DataPool(RECOVERY).read_daily(symbols=['920087.BJ', '920403.BJ'],
        start='2026-09-22', end='2026-09-24', fields=['symbol','date','volume','trading_status'])
    assert len(restored) == 6 and restored.trading_status.isna().all()
    with stock_connection(ROOT, read_only=False) as conn:
        sessions = [r[0] for r in conn.execute("SELECT trade_date FROM market_sessions WHERE trade_date BETWEEN '2026-09-01' AND '2026-09-30' ORDER BY trade_date")]
        assert all(f['trade_date'] in sessions for f in facts)
        applied = apply_daily_changes(conn, bars=bars, dated_facts=facts, market_sessions=sessions, max_elapsed_seconds=180)
        print('committed', json.dumps(applied), flush=True)
        replay = apply_daily_changes(conn, bars=bars, dated_facts=facts, market_sessions=sessions, max_elapsed_seconds=180)
        assert replay['changed_rows'] == 0, replay
pool = DataPool(ROOT)
rows = pool.read_daily(symbols=['920087.BJ','920403.BJ'], start='2026-09-22', end='2026-09-24',
    fields=['symbol','date','volume','trading_status'])
assert len(rows)==6 and (rows.trading_status=='TRADING').all()
features = pool.read_security_daily(symbols=['920087.BJ','920403.BJ'],start='2026-09-22',end='2026-09-30')
assert (features[features.trade_date.astype(str).str[:10].isin(['2026-09-22','2026-09-23','2026-09-24'])].calc_status=='TRADED').all()
summary = pool.read_market_daily(start='2026-09-22',end='2026-09-30')
boards = pool.read_board_daily(start='2026-09-22',end='2026-09-30',kind='concept',limit=100000)
report = {'applied': applied, 'idempotent_replay': replay, 'recovery_public_read_verified': True,
    'recovery_root': str(RECOVERY), 'six_repaired_rows': json.loads(rows.to_json(orient='records',date_format='iso')),
    'features': json.loads(features.to_json(orient='records',date_format='iso')),
    'summary_attrs': summary.attrs,
    'summary_rows': len(summary), 'board_rows': len(boards), 'board_attrs': boards.attrs}
(EVIDENCE / 'repair-receipt.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
print('public read and Enriched checks passed', flush=True)
