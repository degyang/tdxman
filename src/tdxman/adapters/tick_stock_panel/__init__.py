"""Tick Stock Panel provider adapter."""

from .contracts import ProviderError
from .provider import TdxmanProvider

__all__ = ["ProviderError", "TdxmanProvider"]
