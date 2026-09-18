"""sync/update 挂接派生事件：mock 数据源，无网络。

证明：
- 原始日线写入成功不因派生失败回滚；
- 派生失败在报告中可见且可重试；
- 成功时按 changed_dates 触发派生。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pyarrow.parquet as pq
import pytest

from aspool import tdx_online
from aspool.limit_events import initialize_limits
from aspool.pool import DataPool
from aspool.store import daily_path, initialize, record_coverage


def _bar(day: date, close: float, pre_close: float | None = None) -> dict:
    row = {
        "trade_date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "vol": 1000.0,
        "volume": 1000.0,
        "amount": 10000.0,
    }
    if pre_close is not None:
        row["pre_close"] = pre_close
    return row


@pytest.fixture
def pool(tmp_path):
    initialize(tmp_path)
    initialize_limits(tmp_path)
    from aspool.store import catalog

    with catalog(tmp_path) as conn:
        conn.execute(
            "create table if not exists universe (symbol varchar, market varchar, "
            "name varchar, asset_type varchar)"
        )
        conn.execute("delete from universe")
        conn.execute("insert into universe values ('300001','SZ','创业测试','stock')")
    # 已有历史日线：创业板，注册制后 20%。
    # 前置足够会话以排除上市无涨跌幅窗口（创业板 5 日）。
    path = daily_path(tmp_path, "SZ", "300001")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {**_bar(day, 10.0), "market": "SZ", "symbol": "300001", "name": "创业测试"}
        for day in _history()
    ]
    pd.DataFrame(rows).to_parquet(path, index=False)
    record_coverage(
        tmp_path, "300001", "SZ", rows[0]["trade_date"], rows[-1]["trade_date"], len(rows), "test"
    )
    return tmp_path


def _history():
    """14 个平价会话 + 09-09、09-10。"""
    from datetime import timedelta

    out, cursor = [], date(2026, 8, 17)
    while len(out) < 14:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out + [date(2026, 9, 9), date(2026, 9, 10)]


def _patch_fetch(monkeypatch, new_rows):
    """把 fetch_sync 换成返回固定行，杜绝任何网络。

    调用方一律传 limit=1：源码中受限运行会跳过 universe 刷新分支。
    """
    monkeypatch.setattr(tdx_online, "client_factory", lambda *_a, **_k: (object(), None))
    monkeypatch.setattr(
        tdx_online,
        "fetch_sync",
        lambda jobs, factory, fetch, workers, retry: [
            (job, new_rows, None, 0.0) for job in jobs
        ],
    )
    monkeypatch.setattr(tdx_online, "_load_tdxman", lambda: (object, object, object, object))


def test_derivation_failure_keeps_raw_daily(pool, monkeypatch):
    """派生失败不得回滚原始日线。"""
    incoming = [_bar(date(2026, 9, 11), 12.0, pre_close=10.0)]
    _patch_fetch(monkeypatch, incoming)

    def boom(*_args, **_kwargs):
        raise RuntimeError("派生失败")

    monkeypatch.setattr("aspool.limit_events.compute_limit_events", boom)

    before = pq.ParquetFile(daily_path(pool, "SZ", "300001")).read().num_rows
    tdx_online.update_daily(pool, limit=1, workers=1)
    after = pq.ParquetFile(daily_path(pool, "SZ", "300001")).read().num_rows

    # 原始日线已写入且未被回滚。
    assert after == before + 1
    rows = pq.ParquetFile(daily_path(pool, "SZ", "300001")).read().to_pylist()
    assert any(r["trade_date"] == date(2026, 9, 11) for r in rows)
    # 派生未发布。
    assert DataPool(pool).read_limit_events(trade_date=date(2026, 9, 11)).empty


def test_derivation_failure_is_visible_in_report(pool, monkeypatch):
    incoming = [_bar(date(2026, 9, 11), 12.0, pre_close=10.0)]
    _patch_fetch(monkeypatch, incoming)

    def boom(*_args, **_kwargs):
        raise RuntimeError("派生失败")

    monkeypatch.setattr("aspool.limit_events.compute_limit_events", boom)
    tdx_online.update_daily(pool, limit=1, workers=1)

    import json

    report = json.loads((pool / "reports/maintenance/latest.json").read_text())
    assert report["limit_events"]["status"] == "failed"
    assert "派生失败" in report["limit_events"]["error"]
    assert report["limit_events"]["dates"] == ["2026-09-11"]


def test_retry_after_failure_publishes(pool, monkeypatch):
    """派生失败后可用独立入口重试。"""
    incoming = [_bar(date(2026, 9, 11), 12.0, pre_close=10.0)]
    _patch_fetch(monkeypatch, incoming)

    def boom(*_args, **_kwargs):
        raise RuntimeError("派生失败")

    with monkeypatch.context() as failing:
        failing.setattr("aspool.limit_events.compute_limit_events", boom)
        tdx_online.update_daily(pool, limit=1, workers=1)
    assert DataPool(pool).read_limit_events(trade_date=date(2026, 9, 11)).empty

    # 原始数据仍在；独立入口重算成功。
    DataPool(pool).compute_limit_events([date(2026, 9, 11)])
    frame = DataPool(pool).read_limit_events(trade_date=date(2026, 9, 11))
    assert len(frame) == 1
    # 前收 10.00 -> 涨停价 12.00，收盘 12.00 为收盘涨停。
    assert frame.iloc[0]["close_limit_up"] == True  # noqa: E712


def test_successful_sync_triggers_derivation(pool, monkeypatch):
    incoming = [_bar(date(2026, 9, 11), 12.0, pre_close=10.0)]
    _patch_fetch(monkeypatch, incoming)
    tdx_online.update_daily(pool, limit=1, workers=1)

    import json

    report = json.loads((pool / "reports/maintenance/latest.json").read_text())
    assert report["limit_events"]["status"] == "ok"
    assert report["limit_events"]["dates"] == ["2026-09-11"]
    quality = report["field_quality"]["rows"][0]
    assert quality["pre_close"]["valid"] == 1
    assert quality["is_st"]["missing"] == 1
    assert quality["joint_valid"] == 0
    rule_quality = report["limit_events"]["rule_quality"][0]
    assert rule_quality["boards"][0]["board"] == "创业板"
    assert rule_quality["boards"][0]["KNOWN"] == 1
    coverage = DataPool(pool).read_limit_coverage()
    assert len(coverage) == 1


def test_invalid_input_quality_is_separate_in_final_report(pool, monkeypatch):
    path = daily_path(pool, "SZ", "300001")
    rows = pq.ParquetFile(path).read().to_pylist()
    rows[-1]["pre_close"] = 10.0
    rows[-1]["is_st"] = False
    pd.DataFrame(rows).to_parquet(path, index=False)

    old_date = _bar(date(2026, 9, 10), 10.0, pre_close=-1.0)
    old_date["is_st"] = "False"
    new_date = _bar(date(2026, 9, 11), 11.0, pre_close=-1.0)
    new_date["is_st"] = "False"
    _patch_fetch(monkeypatch, [old_date, new_date])
    tdx_online.update_daily(pool, limit=1, workers=1)

    import json

    report = json.loads((pool / "reports/maintenance/latest.json").read_text())
    quality = {row["trade_date"]: row for row in report["field_quality"]["rows"]}
    assert quality["2026-09-10"]["pre_close"]["valid"] == 1
    assert quality["2026-09-10"]["is_st"]["valid"] == 1
    assert quality["2026-09-10"]["joint_valid"] == 1
    assert quality["2026-09-10"]["incoming_invalid"] == {"pre_close": 1, "is_st": 1}
    assert quality["2026-09-11"]["pre_close"]["missing"] == 1
    assert quality["2026-09-11"]["is_st"]["missing"] == 1
    assert quality["2026-09-11"]["incoming_invalid"] == {"pre_close": 1, "is_st": 1}


def test_unchanged_batch_does_not_recompute(pool, monkeypatch):
    """批次无变更时 changed_dates 为空，不触发派生。"""
    import json

    incoming = [_bar(date(2026, 9, 10), 10.0, pre_close=10.0)]
    _patch_fetch(monkeypatch, incoming)
    # 第一次同步让尾部字段稳定。
    tdx_online.update_daily(pool, limit=1, workers=1)
    # 第二次内容完全相同 => 不应再次派生。
    tdx_online.update_daily(pool, limit=1, workers=1)

    report = json.loads((pool / "reports/maintenance/latest.json").read_text())
    assert "limit_events" not in report


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """本模块禁止任何网络连接（显式屏蔽，便于声明"未联网"）。"""
    import socket

    def _blocked(*_a, **_k):
        raise RuntimeError("network access is blocked in limit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
