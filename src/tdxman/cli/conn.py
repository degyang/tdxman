"""CLI 连接工厂：延迟创建通达信客户端。"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from ..client import TdxClient
from ..ex.mac_client import MacExClient
from ..mac.client import MacClient


@contextmanager
def get_mac_client() -> Generator[MacClient, None, None]:
    """创建 MAC 客户端上下文（自动选最快服务器）。

    使用方式::

        with get_mac_client() as client:
            df = client.get_stock_kline(...)
    """
    client = MacClient.from_best_host()
    try:
        client.connect()
        yield client
    finally:
        client.close()


@contextmanager
def get_mac_ex_client() -> Generator[MacExClient, None, None]:
    """创建扩展市场 MAC 客户端上下文（端口 7727）。"""
    client = MacExClient.from_best_host()
    try:
        client.connect()
        yield client
    finally:
        client.close()


@contextmanager
def get_tdx_client() -> Generator[TdxClient, None, None]:
    """创建标准协议客户端上下文（自动选最快服务器）。"""
    client = TdxClient.from_best_host()
    try:
        client.connect()
        yield client
    finally:
        client.close()
