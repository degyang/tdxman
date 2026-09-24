"""Public daily field and error contract, independent of storage engines."""

from functools import wraps

import duckdb


class DataPoolError(ValueError):
    """Public data error with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def public_read(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except DataPoolError:
            raise
        except FileNotFoundError as exc:
            raise DataPoolError("POOL_NOT_FOUND", str(exc)) from exc
        except duckdb.Error as exc:
            raise DataPoolError("DAILY_INVALID", str(exc)) from exc

    return wrapped


# SQL types also define typed nulls when optional source columns are absent.
OPTIONAL_FIELDS = {
    "name": ("VARCHAR", None),
    "pre_close": ("DOUBLE", "CNY/share"),
    "vol_ratio": ("DOUBLE", "ratio"),
    "pct_chg": ("DOUBLE", "percent"),
    "amplitude": ("DOUBLE", "percent"),
    "is_st": ("BOOLEAN", None),
    "trading_status": ("VARCHAR", None),
    "pre_close_source": ("VARCHAR", None),
    "is_st_source": ("VARCHAR", None),
    "trading_status_source": ("VARCHAR", None),
    **{field + "_source": ("VARCHAR", None) for field in
       ("vol_ratio", "turnover_rate", "amplitude", "pct_chg", "float_share", "total_share",
        "float_mv", "total_mv")},
    "total_share": ("DOUBLE", "share"),
    "float_share": ("DOUBLE", "share"),
    "total_mv": ("DOUBLE", "CNY"),
    "float_mv": ("DOUBLE", "CNY"),
    "pe_ttm": ("DOUBLE", "ratio"),
    "pb": ("DOUBLE", "ratio"),
}
BASE_FIELDS = {
    "symbol": ("VARCHAR", None),
    "market": ("VARCHAR", None),
    "code": ("VARCHAR", None),
    "date": ("DATE", None),
    **{key: ("DOUBLE", "CNY/share") for key in ("open", "high", "low", "close")},
    "volume": ("DOUBLE", "share"),
    "amount": ("DOUBLE", "CNY"),
    "turnover_rate": ("DOUBLE", "percent"),
}
DAILY_FIELDS = {**BASE_FIELDS, **OPTIONAL_FIELDS}

# 涨跌停衍生字段（Phase 2+）
LIMIT_FIELDS = {
    "limit_up": ("DOUBLE", "CNY/share"),
    "limit_down": ("DOUBLE", "CNY/share"),
    "limit_pct": ("DOUBLE", "ratio"),  # 0.10 = 10%
    "price_limit_status": ("VARCHAR", None),  # KNOWN/UNKNOWN/NO_LIMIT
}
LIMIT_STATUS_FIELDS = {
    "is_limit_up": ("BOOLEAN", None),
    "is_limit_down": ("BOOLEAN", None),
    "touched_limit_up": ("BOOLEAN", None),
    "touched_limit_down": ("BOOLEAN", None),
    "is_sealed_limit_up": ("BOOLEAN", None),  # 含一字板
    "is_sealed_limit_down": ("BOOLEAN", None),  # 含一字板
    "is_one_word_limit_up": ("BOOLEAN", None),  # 一字涨停板
    "is_one_word_limit_down": ("BOOLEAN", None),  # 一字跌停板
}
CONSECUTIVE_FIELDS = {
    "consecutive_limit_up": ("INTEGER", None),  # null=不确定
    "consecutive_limit_down": ("INTEGER", None),  # null=不确定
    "board_break": ("BOOLEAN", None),  # null=不确定
}
DAILY_DERIVED_FIELDS = {**LIMIT_FIELDS, **LIMIT_STATUS_FIELDS, **CONSECUTIVE_FIELDS}
