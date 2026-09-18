from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq


def _write_etf(root):
    path = root / "lake/bars/daily/market=SZ/symbol=159366/bars.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "symbol": "159366",
                    "market": "SZ",
                    "asset_type": "etf",
                    "name": "测试ETF",
                    "trade_date": date(2009, 12, 31),
                    "open": 1.0,
                    "high": 1.1,
                    "low": 0.9,
                    "close": 1.0,
                    "volume": 100.0,
                    "amount": 100.0,
                },
                {
                    "symbol": "159366",
                    "market": "SZ",
                    "asset_type": "etf",
                    "name": "测试ETF",
                    "trade_date": date(2010, 1, 4),
                    "open": 1.0,
                    "high": 1.1,
                    "low": 0.9,
                    "close": 1.0,
                    "volume": 100.0,
                    "amount": 100.0,
                },
            ]
        ),
        path,
    )


def test_etf_api_is_security_typed_and_starts_at_2010(tmp_path):
    from aspool import DataPool

    _write_etf(tmp_path)
    pool = DataPool(tmp_path)
    frame = pool.read_etf_daily(symbols="SZ.159366", fields=["symbol", "date", "close"])
    assert frame.date.dt.date.tolist() == [date(2010, 1, 4)]
    assert frame.attrs["asset_type"] == "etf"
    assert pool.read_daily().empty
    assert pool.list_etfs().row_count.tolist() == [1]
