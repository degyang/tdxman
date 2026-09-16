"""A-share market data pool and read-only research API."""

from .api_contract import DataPoolError
from .pool import DataPool

__all__ = ["DataPool", "DataPoolError"]
