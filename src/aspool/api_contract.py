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
