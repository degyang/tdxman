"""Minimal DG04 gate reproduction; only the explicit isolated root is writable."""
import json
from datetime import date, timedelta
from pathlib import Path

from aspool import limit_events as limits
from aspool.daily_storage import merge_daily
from aspool.remediation import execute, inputs, plan
from aspool.security_facts import initialize_facts
from aspool.store import catalog, initialize

base = Path('/home/ubuntu/aspool-labs/dg04-gate-review-20260927')
root = base / 'predecessor-drift-pool-v2'
assert not root.exists(), 'Use a fresh isolated fixture'
initialize(root)
initialize_facts(root)
limits.initialize_limits(root)
days = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6)]
rows = [dict(trade_date=d, open=c, high=c, low=c, close=c, pre_close=p,
             is_st=False, volume=100.0, amount=1000.0)
        for d, c, p in zip(days, [10.0, 11.0, 12.1], [10.0, 10.0, 11.0])]
warmup = [dict(rows[0], trade_date=days[0]-timedelta(days=i)) for i in range(5, 0, -1)]
merge_daily(root, 'SZ', '000001', warmup + rows, 'isolated-gate-fixture')
with catalog(root) as conn:
    conn.executemany("INSERT INTO security_calendar VALUES (?,true,'fixture')", [(d,) for d in days])
    conn.execute("INSERT INTO security_lifecycle VALUES ('000001.SZ','1991-04-03',NULL,'fixture','fixture',now())")
limits.compute_limit_events(root, days[:2])
manifest = base / 'fixture-manifest.json'
manifest.write_text('{}')
target, state = base / 'fixture-plan-v2.json', base / 'fixture-state-v2.json'
spec = plan(root, days[2], days[2], manifest, target)
with catalog(root) as conn:
    before = conn.execute('SELECT consecutive_up FROM daily_limit_events WHERE trade_date=?', [days[1]]).fetchone()[0]
    assert before == 1, before
    conn.execute('UPDATE daily_limit_events SET consecutive_up=99 WHERE trade_date=?', [days[1]])
source_unchanged = inputs(root) == spec['source']
result = execute(target, state, max_days=1)
with catalog(root) as conn:
    after = conn.execute('SELECT consecutive_up FROM daily_limit_events WHERE trade_date=?', [days[2]]).fetchone()[0]
evidence = dict(source_root=str(root), source_unchanged=source_unchanged,
                prior_before=before, prior_mutated=99, expected_without_drift=2,
                actual_after=after, accepted_next=result['next'])
assert source_unchanged and after == 100 and result['next'] == 1
(base / 'predecessor-drift-result.json').write_text(json.dumps(evidence, indent=2)+'\n')
print(json.dumps(evidence))
