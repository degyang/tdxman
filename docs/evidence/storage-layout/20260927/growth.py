"""Synthetic history growth on scratch copies using the actual merge_daily path."""
from datetime import timedelta, datetime, timezone
import hashlib
import json
from pathlib import Path
import resource
import statistics
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path.cwd() / 'src'))
from aspool.daily_storage import merge_daily
from aspool.pool import pool_lock

OUT=Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/aspool-layout-assessment-20260927')
ROOT=Path('/home/ubuntu/.aspool')
files=json.loads((OUT/'files.json').read_text())
eligible=sorted([x for x in files if not x['asset_type_column'] and x['end'][:10]=='2026-09-24'], key=lambda r:r['rows'])
selected=[eligible[round(i*(len(eligible)-1)/63)] for i in range(64)]
result={'started_at':datetime.now(timezone.utc).isoformat(), 'method':'64 securities stratified by history length; full schemas; 1x/2x/5x history synthesized by non-overlapping date shifts. Actual merge_daily, temporary roots without catalog/derived publication. Construction excluded. Not a future market forecast or full sync benchmark.', 'cases':[]}
records=[]
with pool_lock(ROOT):
    for item in selected:
        path=Path(item['path'])
        table=pq.ParquetFile(path).read()
        records.append((item,table))

for factor in [1,2,5]:
    base=OUT/f'write_growth_{factor}x'
    base.mkdir(exist_ok=True)
    samples=[]
    for index,(item,table) in enumerate(records):
        days=table['trade_date'].to_pylist()
        chunks=[]
        for block in reversed(range(factor)):
            dates=pa.array([d-timedelta(days=10000*block) for d in days],type=table['trade_date'].type)
            chunks.append(table.set_column(table.column_names.index('trade_date'),'trade_date',dates))
        expanded=pa.concat_tables(chunks)
        target=base/f'{index}.parquet'
        pq.write_table(expanded,target,compression='zstd')
        incoming=expanded.slice(len(expanded)-1).to_pylist()[0]
        incoming['trade_date']+=timedelta(days=1)
        market=Path(item['path']).parent.parent.name.split('=')[1]
        symbol=Path(item['path']).parent.name.split('=')[1]
        samples.append((target,market,symbol,incoming,len(expanded)))
    for iteration in range(3):
        elapsed=0.0
        changed=0
        before_bytes=after_bytes=before_rows=0
        for target,market,symbol,incoming,n in samples:
            if iteration:
                incoming['trade_date']+=timedelta(days=1)
            before_bytes+=target.stat().st_size
            before_rows+=n+iteration
            tick=time.perf_counter()
            count=merge_daily(base,market,symbol,[incoming],source='assessment:synthetic',coverage=[],_path=target)
            elapsed+=time.perf_counter()-tick
            changed+=count
            after_bytes+=target.stat().st_size
        result['cases'].append({'history_factor':factor,'iteration':iteration,'securities':len(samples),'changed_rows':changed,'input_history_rows':before_rows,'seconds':elapsed,'input_file_bytes':before_bytes,'rewritten_file_bytes':after_bytes,'row_rewrite_amplification':(before_rows+64)/max(changed,1)})
        print(json.dumps(result['cases'][-1]),flush=True)
    tick=time.perf_counter()
    unchanged=sum(merge_daily(base,market,symbol,[incoming],source='assessment:synthetic',coverage=[],_path=target) for target,market,symbol,incoming,n in samples)
    assert unchanged==0
    result.setdefault('no_op',[]).append({'history_factor':factor,'seconds':time.perf_counter()-tick,'changed_rows':unchanged})
    (OUT/'growth-results.json').write_text(json.dumps(result,indent=2)+'\n')
result['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
result['finished_at']=datetime.now(timezone.utc).isoformat()
(OUT/'growth-results.json').write_text(json.dumps(result,indent=2)+'\n')
