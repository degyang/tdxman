"""Read-only public API baseline and the existing symbol-path fast case."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

import duckdb
sys.path.insert(0, str(Path.cwd() / 'src'))
from aspool import DataPool
from aspool.pool import pool_lock
OUT=Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/aspool-layout-assessment-20260927')
ROOT=Path('/home/ubuntu/.aspool')
r={'started_at':datetime.now(timezone.utc).isoformat(),'cases':[],'note':'Local page cache not flushed. Public API includes validation, pandas materialization and dated-fact overlays; raw layout microbenchmarks do not.'}
pool=DataPool(ROOT)
fields=['symbol','date','close','pre_close','pct_chg','amount','is_st','turnover_rate']
for name,start,end,symbols in [('market_1day','2026-09-24','2026-09-24',None),('market_60calendar_days','2026-07-27','2026-09-24',None),('one_symbol_full_history','2000-01-01','2026-09-24',['000001.SZ'])]:
    times=[]
    for i in range(3):
        tick=time.perf_counter()
        frame=pool.read_research_daily(start=start,end=end,symbols=symbols,fields=fields)
        times.append(time.perf_counter()-tick)
        item={'workload':name,'iteration':i,'seconds':times[-1],'rows':len(frame)}
        print(json.dumps(item),flush=True)
        del frame
    r['cases'].append({'workload':name,'seconds':times,'median_seconds':statistics.median(times),'rows':item['rows']})
    (OUT/'public-results.json').write_text(json.dumps(r,indent=2)+'\n')

with pool_lock(ROOT):
    path=ROOT/'lake/bars/daily/market=SZ/symbol=000001/bars.parquet'
    c=duckdb.connect()
    c.execute("SET threads=2")
    c.execute("SET memory_limit='512MB'")
    c.read_parquet(str(path),hive_partitioning=True).create_view('bars')
    times=[]
    for i in range(3):
        tick=time.perf_counter()
        row=c.execute('SELECT count(*),sum(close),sum(amount),sum(volume) FROM bars').fetchone()
        times.append(time.perf_counter()-tick)
    r['direct_single_symbol']={'seconds':times,'median_seconds':statistics.median(times),'aggregate':row}
    c.close()
with duckdb.connect(str(OUT/'native.duckdb'),read_only=True) as c:
    r['symbol_year_partition_count']=c.execute('SELECT count(*) FROM (SELECT DISTINCT market,symbol,year(trade_date) FROM bars)').fetchone()[0]
    r['stock_rows']=c.execute('SELECT count(*) FROM bars').fetchone()[0]
r['finished_at']=datetime.now(timezone.utc).isoformat()
(OUT/'public-results.json').write_text(json.dumps(r,indent=2)+'\n')
print('done',json.dumps(r),flush=True)
