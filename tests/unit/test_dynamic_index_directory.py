import duckdb
import pandas as pd

from aspool.index_lists import collect
from aspool.securities import active_indices, publish_index_directory


def test_live_collection_uses_hy2_gn_fg_zs_and_only_excludes_yesterday_style():
    class Client:
        def get_board_list(self, board_type, count):
            name = board_type.name
            values = {
                "HY2": [{"market": 1, "code": "881001", "name": "二级行业"}],
                "GN": [{"market": 1, "code": "880001", "name": "概念"}],
                "FG": [
                    {"market": 1, "code": "880712", "name": "微小盘股"},
                    {"market": 1, "code": "880770", "name": "昨日上榜"},
                ],
            }
            return pd.DataFrame(values[name])

        def get_stock_quotes_list(self, *args, **kwargs):
            return pd.DataFrame(
                [
                    {"market": 1, "code": "999999", "name": "上证指数"},
                    {"market": 2, "code": "899050", "name": "北证50"},
                    {"market": 1, "code": "880712", "name": "微小盘股"},
                ]
            )

    result, excluded = collect(Client())
    records = {(row["code"], row["market"]): row for row in result["indices"]}
    assert ("000001", "SH") in records
    assert records[("880712", "SH")]["source"] == ["FG", "ZS"]
    assert ("899050", "BJ") in records
    assert excluded == [{"market": "SH", "code": "880770", "name": "昨日上榜"}]


def test_publish_index_directory_tracks_memberships_and_preserves_history_scope(tmp_path):
    root = tmp_path / "data"
    first = [
        {"market": "SH", "code": "881001", "name": "行业", "source": ["HY2"]},
        {"market": "SH", "code": "880712", "name": "微小盘", "source": ["FG", "ZS"]},
    ]
    report = publish_index_directory(root, first, benchmark_symbols={"880712.SH"})
    assert report["listed"] == 2 and report["added"] == 2
    assert active_indices(root) == [
        {
            "symbol": "880712.SH",
            "code": "880712",
            "market": "SH",
            "name": "微小盘",
            "source": ["FG", "ZS"],
        },
        {
            "symbol": "881001.SH",
            "code": "881001",
            "market": "SH",
            "name": "行业",
            "source": ["HY2"],
        },
    ]
    second = [{"market": "SH", "code": "880712", "name": "微小盘新名", "source": ["ZS"]}]
    report = publish_index_directory(root, second)
    assert report["changed"] == 1 and report["inactive"] == 1
    with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as conn:
        assert conn.execute(
            "SELECT active FROM securities WHERE symbol='881001.SH'"
        ).fetchone() == (False,)
        assert conn.execute(
            "SELECT active FROM index_memberships WHERE symbol='880712.SH' AND category='FG'"
        ).fetchone() == (False,)
