from __future__ import annotations

import struct

from tdxman.mac.commands.tick_charts import TickChartsCmd


def test_parse_response_uses_total_ticks_for_partial_first_day() -> None:
    """A live first day can have fewer ticks than a completed trading day."""
    count, page_size, total = 2, 2, 3
    tail_offset = 71 + total * 14
    tail_size = struct.calcsize("<44sBHf5x2I5ffIf12s2fI")
    body = bytearray(tail_offset + tail_size)

    struct.pack_into("<H22s", body, 0, 1, b"600519")
    struct.pack_into("<5I", body, 24, 20260916, 20260915, 0, 0, 0)
    struct.pack_into("<5f", body, 44, 1500.0, 1490.0, 0.0, 0.0, 0.0)
    struct.pack_into("<HBHH", body, 64, count, 0, page_size, total)

    for index, minutes in enumerate((570, 570, 571)):
        struct.pack_into("<HffHH", body, 71 + index * 14, minutes, 1.0, 1.0, 1, 0)

    chart = TickChartsCmd(1, "600519", days=2).parse_response(bytes(body))

    assert [len(day.ticks) for day in chart.charts] == [1, 2]
    assert [tick.time.strftime("%H:%M") for day in chart.charts for tick in day.ticks] == [
        "09:30",
        "09:30",
        "09:31",
    ]
