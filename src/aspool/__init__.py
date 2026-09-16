"""A-share market data pool and read-only research API."""

from .fundamentals import refresh_fundamentals
from .pool import DataPool

__all__ = ["DataPool", "refresh_fundamentals"]
