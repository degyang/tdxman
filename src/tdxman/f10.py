"""Read-only client for TDX's 7615 TQLEX company and financial pages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .exceptions import TdxCommandError, TdxConnectionError, TdxDecodeError
from .models.enums import Market

DEFAULT_TQLEX_URL = "http://static.tdx.com.cn:7615/TQLEX"
F10_SECTIONS = (
    "profile",
    "report",
    "business",
    "dividends",
    "shareholders",
    "topics",
    "announcements",
    "news",
    "governance",
    "forecast",
)


@dataclass(frozen=True)
class F10Table:
    key: str
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class F10Response:
    entry: str
    tables: tuple[F10Table, ...]
    raw: dict[str, Any]

    def to_frame(self) -> pd.DataFrame:
        rows = [{"result_set": table.key, **row} for table in self.tables for row in table.rows]
        return pd.DataFrame(rows)


class F10Client:
    """Query supported read-only TQLEX Entries without a 7709 connection."""

    def __init__(self, *, base_url: str = DEFAULT_TQLEX_URL, timeout: float = 8.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def query(
        self,
        market: Market,
        code: str,
        section: str = "profile",
        *,
        report_type: str = "zcfzb",
        report_date: str | None = None,
    ) -> F10Response:
        if section not in F10_SECTIONS:
            raise ValueError(f"不支持的 F10 栏目：{section}")
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise ValueError("F10 证券代码应为六位数字")
        self._verify_identity(market, code)

        if section == "profile":
            return self._params("CWServ.tdxf10_gg_gsgk", "8", code, "")
        if section == "report":
            return self._params("CWServ.tdxf10_gg_cwfx", code, report_type, "")
        if section == "business":
            selected_date = report_date
            if selected_date is None:
                periods = self._params("CWServ.tdxf10_gg_comreq", "zygcfx", code)
                selected_date = next(
                    (
                        str(row.get("T002") or row.get("t002"))
                        for table in periods.tables
                        for row in table.rows
                        if row.get("T002") or row.get("t002")
                    ),
                    None,
                )
            if selected_date is None:
                raise TdxDecodeError("F10 未返回主营构成报告期；请指定 --report-date")
            return self._params("CWServ.tdxf10_gg_jyfx", code, "zygc", selected_date)
        if section == "dividends":
            return self._params("CWServ.tdxf10_gg_fhrz", code, "fh")
        if section == "shareholders":
            return self._params("CWServ.tdxf10_gg_gdyj", code, "gdzjcjh", "", "", "1", "1", "20")
        if section == "topics":
            return self._params("CWServ.tdxf10_gg_rdtc", code, "zttzbkz")
        if section in {"announcements", "news"}:
            kind = "gg" if section == "announcements" else "xw"
            return self._post(
                "CWSearch.tzx_rcache",
                {
                    "action": "get",
                    "key": f"{kind}:{market.value}_{code}",
                    "bin": "1",
                    "qsid": "tdx",
                },
            )
        if section == "governance":
            return self._params("CWServ.tdxf10_gg_zbyz", "wgcl", code, "")
        return self._params("CWServ.tdxf10_gg_ybpj", code, "ylyctj")

    def _verify_identity(self, market: Market, code: str) -> None:
        # Most CWServ Entries accept only a six-digit code. Verify the market
        # separately so an index-like SH code cannot return an SZ stock page.
        response = self._params("CWServ.tdxf10_gg_comreq", "gpquery", code)
        rows = [row for table in response.tables for row in table.rows]
        if not rows:
            raise TdxCommandError(f"F10 未找到证券 {code}.{market.name}")
        identity = rows[0]
        if str(identity.get("T002")) != code or str(identity.get("sc")) != str(market.value):
            raise TdxCommandError(f"F10 证券市场或代码不匹配：{code}.{market.name}")

    def _params(self, entry: str, *params: str) -> F10Response:
        return self._post(entry, {"Params": list(params)})

    def _post(self, entry: str, body: dict[str, Any]) -> F10Response:
        url = f"{self.base_url}?{urlencode({'Entry': entry})}"
        request = Request(
            url,
            data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "tdxman/1.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            raise TdxConnectionError(f"F10 HTTP {exc.code}: {entry}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise TdxConnectionError(f"F10 请求失败：{entry}: {exc}") from exc
        try:
            raw = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TdxDecodeError(f"F10 返回的 JSON 无效：{entry}") from exc
        if not isinstance(raw, dict):
            raise TdxDecodeError("F10 返回值不是 JSON 对象")
        error_code = raw.get("ErrorCode")
        if error_code is not None and str(error_code) != "0":
            raise TdxCommandError(f"F10 服务端错误 {error_code}: {raw.get('ErrorMessage', '')}")
        result_sets = raw.get("ResultSets") or []
        if not isinstance(result_sets, list):
            raise TdxDecodeError("F10 ResultSets 格式无效")
        tables = tuple(_parse_table(item, index) for index, item in enumerate(result_sets))
        return F10Response(entry=entry, tables=tables, raw=raw)


def _parse_table(item: Any, index: int) -> F10Table:
    if not isinstance(item, dict):
        raise TdxDecodeError("F10 ResultSet 格式无效")
    columns = item.get("ColName")
    if columns is None:
        descriptions = item.get("ColDes") or []
        if not isinstance(descriptions, list):
            raise TdxDecodeError("F10 ColDes 格式无效")
        columns = [
            str(description.get("Name", "")) if isinstance(description, dict) else str(description)
            for description in descriptions
        ]
    if not isinstance(columns, list):
        raise TdxDecodeError("F10 ColName 格式无效")
    content = item.get("Content") or []
    if not isinstance(content, list):
        raise TdxDecodeError("F10 Content 格式无效")
    rows: list[dict[str, Any]] = []
    for record in content:
        values = record if isinstance(record, list) else [record]
        row: dict[str, Any] = {}
        for position, value in enumerate(values):
            name = (
                str(columns[position])
                if position < len(columns) and columns[position]
                else f"col_{position}"
            )
            key = name
            duplicate = 2
            while key in row:
                key = f"{name}__{duplicate}"
                duplicate += 1
            row[key] = value
        rows.append(row)
    return F10Table(key=str(item.get("ResultSetKey") or f"table{index}"), rows=tuple(rows))
