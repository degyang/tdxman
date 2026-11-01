"""Read-only production inventory and isolated storage-layout microbenchmark."""
import collections
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import statistics
import sys
import time

import duckdb
import pyarrow.parquet as pq

sys.path.insert(0, str(Path.cwd() / 'src'))
from aspool.pool import pool_lock

ROOT = Path('/home/ubuntu/.aspool')
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/aspool-layout-assessment-20260927')
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / 'results.json'
result = {'started_at': datetime.now(timezone.utc).isoformat(),
          'duckdb': duckdb.__version__, 'method': 'Production read-only; scratch files only. Warm-cache microbenchmarks, not end-to-end service SLOs.',
          'threads': 2, 'memory_limit': '512MB', 'cases': []}

def save():
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n')

def fingerprint(files):
    return hashlib.sha256('\n'.join(f'{p}:{p.stat().st_size}:{p.stat().st_mtime_ns}' for p in files).encode()).hexdigest()

def connection(path=':memory:', read_only=False):
    c = duckdb.connect(str(path), read_only=read_only)
    c.execute("SET threads=2")
    c.execute("SET memory_limit='512MB'")
    c.execute("SET temp_directory=?", [str(OUT / 'spill')])
    return c

with pool_lock(ROOT):
    start = time.perf_counter()
    files = sorted((ROOT / 'lake/bars/daily').rglob('*.parquet'))
    result['enumerate_seconds'] = time.perf_counter() - start
    initial = fingerprint(files)
    records = []
    groups = collections.Counter()
    schemas = collections.Counter()
    col_sizes = collections.Counter()
    start = time.perf_counter()
    for p in files:
        pf = pq.ParquetFile(p)
        md = pf.metadata
        groups[md.num_row_groups] += 1
        schemas[str(pf.schema_arrow.remove_metadata())] += 1
        ix = md.schema.names.index('trade_date')
        dates = []
        for gi in range(md.num_row_groups):
            rg = md.row_group(gi)
            st = rg.column(ix).statistics
            if st and st.has_min_max:
                dates.extend([st.min, st.max])
            for ci in range(rg.num_columns):
                col = rg.column(ci)
                col_sizes[col.path_in_schema] += col.total_compressed_size
        records.append({'path': str(p), 'rows': md.num_rows, 'bytes': p.stat().st_size,
                        'footer_bytes': md.serialized_size, 'row_groups': md.num_row_groups,
                        'start': str(min(dates)), 'end': str(max(dates)),
                        'asset_type_column': 'asset_type' in pf.schema_arrow.names})
    result['inventory'] = {'files': len(files), 'rows': sum(r['rows'] for r in records),
         'bytes': sum(r['bytes'] for r in records), 'footer_bytes': sum(r['footer_bytes'] for r in records),
         'row_groups_histogram': groups, 'schema_variants': len(schemas),
         'rows_median': statistics.median(r['rows'] for r in records),
         'rows_max': max(r['rows'] for r in records), 'column_compressed_bytes': col_sizes,
         'footer_inventory_seconds': time.perf_counter() - start,
         'files_ending_20260924': sum(r['end'][:10] == '2026-09-24' for r in records),
         'stock_files_ending_20260924_bytes': sum(r['bytes'] for r in records if not r['asset_type_column'] and r['end'][:10]=='2026-09-24')}
    (OUT / 'files.json').write_text(json.dumps(records, indent=2))
    save()
    print('inventory', json.dumps(result['inventory']), flush=True)
    raw = connection()
    tick = time.perf_counter()
    raw.read_parquet([str(p) for p in files], union_by_name=True, hive_partitioning=True).create_view('all_bars')
    result['initial_bind_seconds'] = time.perf_counter()-tick
    raw.execute("CREATE VIEW bars AS SELECT CAST(trade_date AS DATE) trade_date, CAST(symbol AS VARCHAR) symbol, market, open, high, low, close, coalesce(volume,vol) volume, amount FROM all_bars WHERE asset_type IS NULL OR asset_type <> 'etf'")
    projection = 'count(*) n, sum(close) close_sum, sum(amount) amount_sum, sum(volume) volume_sum'
    workloads = {
      'market_1day': "trade_date = DATE '2026-09-24'",
      'market_60calendar_days': "trade_date BETWEEN DATE '2026-07-27' AND DATE '2026-09-24'",
      'market_5years': "trade_date BETWEEN DATE '2021-09-25' AND DATE '2026-09-24'",
      'one_symbol_full_history': "market = 'SZ' AND symbol='000001'",
    }
    def bench(c, layout, where_extra=''):
        for name, clause in workloads.items():
            timings=[]
            values=[]
            sql=f'SELECT {projection} FROM bars WHERE {clause}'
            for i in range(3):
                tick=time.perf_counter()
                row=c.execute(sql).fetchone()
                timings.append(time.perf_counter()-tick)
                values.append(row)
            assert all(x[0]==values[0][0] and all(math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-6) for a,b in zip(x[1:],values[0][1:])) for x in values), (layout,name)
            item={'layout':layout,'workload':name,'seconds':timings,'median_seconds':statistics.median(timings),'aggregate':values[0]}
            result['cases'].append(item)
            save()
            print('case',json.dumps(item),flush=True)
    bench(raw,'original_symbol_parquet')
    # Collect a DuckDB profile separately from timing runs.
    raw.execute("PRAGMA enable_profiling='json'")
    raw.execute("SET profiling_output=?", [str(OUT/'raw_day_profile.json')])
    raw.execute(f'SELECT {projection} FROM bars WHERE '+workloads['market_1day']).fetchall()
    raw.execute('PRAGMA disable_profiling')
    # Copy only the common raw fields; this is not a replacement pool schema.
    native_existed = (OUT / 'native.duckdb').exists()
    native = connection(OUT / 'native.duckdb')
    native.read_parquet([str(p) for p in files],union_by_name=True,hive_partitioning=True).create_view('source_bars')
    tick=time.perf_counter()
    native.execute("CREATE TABLE IF NOT EXISTS bars AS SELECT CAST(trade_date AS DATE) trade_date, CAST(symbol AS VARCHAR) symbol, market, open, high, low, close, coalesce(volume,vol) volume, amount FROM source_bars WHERE asset_type IS NULL OR asset_type <> 'etf' ORDER BY trade_date,market,symbol")
    native.execute('CHECKPOINT')
    result['native_build_seconds']=None if native_existed else time.perf_counter()-tick
    result['native_reused_from_previous_attempt']=native_existed
    result['native_bytes']=(OUT/'native.duckdb').stat().st_size
    result['stock_rows_by_year']=native.execute('SELECT year(trade_date) AS yr,count(*) AS n,count(distinct market||symbol) AS symbols FROM bars GROUP BY 1 ORDER BY 1').fetchall()
    result['current_day_stock_rows']=native.execute("SELECT count(*) FROM bars WHERE trade_date=DATE '2026-09-24'").fetchone()[0]
    assert initial == fingerprint(files), 'Production daily file metadata changed during snapshot.'
    result['production_fingerprint']=initial
    result['production_files_unchanged']=True
    raw.close()
    save()

bench(native,'native_duckdb_date_sorted')
tick=time.perf_counter()
native.execute("COPY (SELECT *, year(trade_date) yr, month(trade_date) mo FROM bars ORDER BY trade_date,market,symbol) TO ? (FORMAT PARQUET, PARTITION_BY (yr,mo), COMPRESSION ZSTD, ROW_GROUP_SIZE 32768)",[str(OUT/'monthly')])
result['monthly_build_seconds']=time.perf_counter()-tick
monthly_files=sorted((OUT/'monthly').rglob('*.parquet'))
result['monthly_files']=len(monthly_files)
result['monthly_bytes']=sum(p.stat().st_size for p in monthly_files)
monthly=connection()
tick=time.perf_counter()
monthly.read_parquet([str(p) for p in monthly_files],hive_partitioning=True).create_view('bars')
result['monthly_bind_seconds']=time.perf_counter()-tick
bench(monthly,'monthly_parquet_date_sorted')
# Verify equivalent raw aggregates across layouts with explicit float tolerance.
import math
for name in workloads:
    cases=[r for r in result['cases'] if r['workload']==name]
    ref=cases[0]['aggregate']
    for case in cases[1:]:
        actual=case['aggregate']
        assert actual[0]==ref[0]
        assert all(math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-6) for a,b in zip(actual[1:],ref[1:])),(name,actual,ref)
result['raw_aggregate_equivalence']=True
result['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
result['finished_at']=datetime.now(timezone.utc).isoformat()
save()
print('done',REPORT,flush=True)
