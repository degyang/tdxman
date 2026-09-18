from datetime import date

from aspool.fundamentals import _quote_bar
from aspool.st_source import classify_st_name


def test_st_name_classifier_requires_a_prefix_and_preserves_unknown():
    assert classify_st_name("*ST美丽") is True
    assert classify_st_name("ST海王") is True
    assert classify_st_name("S*ST样本") is True
    assert classify_st_name("平安银行") is False
    assert classify_st_name("TEST科技") is False
    assert classify_st_name("") is None
    assert classify_st_name(None) is None


def test_quote_bar_records_same_day_name_evidence():
    row = _quote_bar(
        {
            "code": "000010",
            "name": "*ST美丽",
            "pre_close": 1.56,
            "open": 1.56,
            "high": 1.60,
            "low": 1.55,
            "close": 1.58,
            "vol": 10,
            "amount": 1000,
        },
        date(2026, 9, 18),
    )
    assert row["is_st"] is True
    assert row["is_st_source"] == "tdxman:quote_name"
    assert row["is_st_name_date"] == date(2026, 9, 18)


def test_quote_bar_does_not_default_missing_name_to_false():
    row = _quote_bar(
        {
            "code": "000001",
            "name": None,
            "pre_close": 10.0,
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.1,
            "vol": 10,
            "amount": 1000,
        },
        date(2026, 9, 18),
    )
    assert "is_st" not in row
