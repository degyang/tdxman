"""CLI 输出格式化：JSON（默认）、表格、CSV。"""

from __future__ import annotations

from functools import wraps
from pathlib import Path

import click
import pandas as pd

_OUTPUT_FORMATS = click.Choice(["json", "table", "csv"], case_sensitive=False)
_OUTPUT_SUFFIXES = {"json": ".json", "table": ".md", "csv": ".csv"}
_BASIC_TABLE_COLUMNS = (
    "date",
    "datetime",
    "time",
    "market",
    "code",
    "name",
    "price",
    "close",
    "pre_close",
    "open",
    "high",
    "low",
    "change",
    "change_pct",
    "vol",
    "volume",
    "amount",
    "turnover_rate",
    "market_cap",
    "super_in",
    "super_out",
    "large_in",
    "large_out",
    "medium_in",
    "medium_out",
    "small_in",
    "small_out",
    "main_in",
    "main_out",
    "main_net",
    "small_net",
    "mid_net",
    "large_net",
    "up_count",
    "down_count",
    "zong_zichan",
    "jing_zichan",
    "zhuying_shouru",
    "jing_lirun",
    "meigujing_zichan",
)


def output_options(func):
    """Add the standard output options to a data command."""
    command = func

    @wraps(command)
    def with_output_fields(*args, **kwargs):
        kwargs.pop("fields", None)
        return command(*args, **kwargs)

    func = click.option(
        "--fields",
        type=click.Choice(["basic", "all"]),
        default="basic",
        show_default=True,
        help="表格输出字段；JSON 和 CSV 始终包含全部字段",
    )(with_output_fields)
    func = click.option(
        "-o",
        "--output",
        "output_path",
        type=click.Path(path_type=Path),
        default=None,
        help="写入文件或目录；目录内文件名默认为标的代码",
    )(func)
    func = click.option(
        "-f",
        "--format",
        "output_fmt",
        type=_OUTPUT_FORMATS,
        default="json",
        show_default=True,
        help="输出格式",
    )(func)
    return func


_PRICE_COLUMNS = frozenset(
    {
        "pre_close",
        "open",
        "high",
        "low",
        "close",
        "price",
        "avg",
        "avg_price",
        "buy_price_limit",
        "sell_price_limit",
        "pre_iopv",
        "iopv",
        "security_type_price",
    }
)

_INTEGER_COLUMNS = frozenset(
    {
        "last_volume",
        "lot_size",
        "lot_size_info",
        "price_decimal_info",
        "stock_tag_flags",
        "vol",
    }
)

_FINANCE_TWO_DECIMAL_COLUMNS = frozenset(
    {
        "b_gu",
        "changqi_fuzhai",
        "cunhuo",
        "faren_gu",
        "faqiren_faren_gu",
        "guding_zichan",
        "guojia_gu",
        "h_gu",
        "jing_zichan",
        "jing_lirun",
        "jingying_xianjinliu",
        "liudong_fuzhai",
        "liudong_zichan",
        "liutong_guben",
        "lirun_zonghe",
        "meigujing_zichan",
        "shuihou_lirun",
        "touzi_shouyu",
        "weifen_lirun",
        "wuxing_zichan",
        "yingshou_zhangkuan",
        "yingye_lirun",
        "ziben_gongjijin",
        "zong_guben",
        "zong_xianjinliu",
        "zong_zichan",
        "zhigong_gu",
        "zhuying_lirun",
        "zhuying_shouru",
    }
)

_FUND_FLOW_AMOUNT_COLUMNS = frozenset(
    {
        "super_in",
        "super_out",
        "large_in",
        "large_out",
        "medium_in",
        "medium_out",
        "small_in",
        "small_out",
    }
)


def _render_price(value: float) -> str:
    return f"{value:.2f}" if pd.notna(value) else ""


def _is_two_decimal_column(column: str) -> bool:
    return (
        column in _PRICE_COLUMNS
        or column in _FINANCE_TWO_DECIMAL_COLUMNS
        or column in _FUND_FLOW_AMOUNT_COLUMNS
        or column.endswith("_price")
        or "amount" in column
        or "market_cap" in column
    )


def _format_numeric_values(df: pd.DataFrame) -> pd.DataFrame:
    """Round CLI numeric output according to its field semantics."""
    result = df.copy()
    for column in result.columns:
        if not pd.api.types.is_numeric_dtype(result[column]):
            continue
        if _is_two_decimal_column(column):
            result[column] = result[column].round(2)
        elif column in _INTEGER_COLUMNS:
            result[column] = result[column].round().astype("Int64")
        else:
            result[column] = result[column].round(4)
    return result


def _render_decimal(value: float) -> str:
    if pd.isna(value):
        return ""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _render_integer(value: float) -> str:
    return str(int(value)) if pd.notna(value) else ""


def _format_price_display(df: pd.DataFrame) -> pd.DataFrame:
    """Render CLI numeric values without binary floating-point artifacts."""
    result = df.copy()
    for column in result.columns:
        if not pd.api.types.is_numeric_dtype(result[column]):
            continue
        if _is_two_decimal_column(column):
            result[column] = result[column].map(_render_price)
        elif column in _INTEGER_COLUMNS:
            result[column] = result[column].map(_render_integer)
        elif pd.api.types.is_float_dtype(result[column]):
            result[column] = result[column].map(_render_decimal)
    return result


def format_output(df: pd.DataFrame, fmt: str = "json") -> str:
    """将 DataFrame 格式化为指定输出格式。"""
    if df.empty:
        return "[]" if fmt == "json" else ""

    df = _format_numeric_values(df)
    if fmt == "json":
        result: str = df.to_json(orient="records", force_ascii=False, date_format="iso")
        return result
    if fmt == "csv":
        return str(_format_price_display(df).to_csv(index=False))
    if fmt == "table":
        return _render_table(df)
    raise click.UsageError(f"不支持的输出格式: {fmt}")


def print_output(
    df: pd.DataFrame,
    fmt: str = "json",
    output_path: Path | None = None,
    *,
    filename: str | None = None,
    split_rows: bool = False,
) -> None:
    """Format output to stdout or a file/directory.

    A directory receives a file named after the requested symbol.  Multi-symbol
    quote requests write one response per symbol and cannot target one file.
    """
    fmt = fmt.lower()
    if fmt == "table" and _table_fields() == "basic":
        df = _select_basic_table_fields(df)
    if output_path is None:
        text = format_output(df, fmt)
        if text:
            click.echo(text)
        return

    suffix = _OUTPUT_SUFFIXES[fmt]
    path = output_path.expanduser()
    is_directory = path.suffix == "" or path.is_dir()
    if filename is None and "code" in df.columns and df["code"].nunique() == 1:
        filename = str(df["code"].iloc[0])
    safe_name = filename or "data"

    if split_rows:
        if not is_directory:
            raise click.UsageError("多个标的不能写入单一文件；请将 --output 指定为目录")
        if "code" not in df.columns:
            raise click.UsageError("无法按标的拆分输出：返回结果缺少 code 字段")
        path.mkdir(parents=True, exist_ok=True)
        for code, group in df.groupby("code", sort=False):
            _write_output(path / f"{code}{suffix}", group, fmt)
        return

    destination = path / f"{safe_name}{suffix}" if is_directory else path
    if destination.suffix.lower() != suffix:
        raise click.UsageError(f"--format {fmt} 的输出文件扩展名必须为 {suffix}")
    _write_output(destination, df, fmt)


def _write_output(path: Path, df: pd.DataFrame, fmt: str) -> None:
    """Write one formatted response, creating its parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "table":
        path.write_text(_render_markdown(df), encoding="utf-8")
    else:
        path.write_text(format_output(df, fmt), encoding="utf-8")


def _table_fields() -> str:
    """Return the current command's table profile, if invoked through Click."""
    ctx = click.get_current_context(silent=True)
    return str(ctx.params.get("fields", "all")) if ctx is not None else "all"


def _select_basic_table_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the commonly useful columns for terminal-sized tables."""
    columns = [column for column in _BASIC_TABLE_COLUMNS if column in df.columns]
    return df.loc[:, columns] if columns else df


def print_error(msg: str) -> None:
    """输出错误消息到 stderr。"""
    click.echo(f"错误: {msg}", err=True)


def _render_table(df: pd.DataFrame) -> str:
    """将 DataFrame 渲染为人类可读的文本表格。"""
    if df.empty:
        return "(无数据)"

    display_df = _format_price_display(df)
    for col in display_df.columns:
        if display_df[col].dtype == object:
            display_df[col] = display_df[col].astype(str).str.slice(0, 30)

    try:
        import tabulate

        return str(tabulate.tabulate(display_df, headers="keys", tablefmt="grid", showindex=False))
    except ImportError:
        lines: list[str] = []
        cols = list(display_df.columns)
        header = " | ".join(str(c) for c in cols)
        sep = "-+-".join("-" * min(len(str(c)), 30) for c in cols)
        lines.append(header)
        lines.append(sep)
        for _, row in display_df.iterrows():
            line = " | ".join(str(v)[:30] for v in row.values)
            lines.append(line)
        return "\n".join(lines)


def _render_markdown(df: pd.DataFrame) -> str:
    """Render a table response as GitHub-flavoured Markdown for files."""
    if df.empty:
        return ""
    display_df = _format_price_display(df)
    columns = [str(column) for column in display_df.columns]

    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", "<br>")

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in display_df.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(cell(value) for value in row) + " |")
    return "\n".join(lines) + "\n"
