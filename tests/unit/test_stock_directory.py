import pandas as pd

from aspool.securities import active_securities, publish_directory
from aspool.store import initialize
from aspool.universe import refresh_stock_universe


def test_stock_directory_preserves_live_kcb_cdr_without_admitting_indices(tmp_path, monkeypatch):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_stock_quotes_list(self, category, **kwargs):
            entries = {
                "SH": [
                    {"code": "689009", "name": "九号公司-WD"},
                    {"code": "600519", "name": "贵州茅台"},
                    {"code": "000001", "name": "上证指数"},
                ],
                "SZ": [{"code": "000001", "name": "平安银行"}],
                "BJ": [{"code": "920011", "name": "北交所股票"}],
            }
            return pd.DataFrame(entries[category.name])

    monkeypatch.setattr("tdxman.mac.client.MacClient", lambda **kwargs: Client())
    initialize(tmp_path)
    publish_directory(
        tmp_path,
        [{"symbol": "689009.SH", "code": "689009", "market": "SH", "name": "九号公司-WD"}],
        asset_type="stock",
    )
    report = refresh_stock_universe(tmp_path)
    assert report["listed"] == 4
    assert report["inactive"] == 0
    assert {r[0] for r in active_securities(tmp_path, "stock")} == {
        "689009.SH",
        "600519.SH",
        "000001.SZ",
        "920011.BJ",
    }
    assert refresh_stock_universe(tmp_path)["inactive"] == 0
