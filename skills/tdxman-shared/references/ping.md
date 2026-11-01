# `tdxman ping`

```bash
tdxman ping --timeout 3 --format table
```

`--timeout` 是每台服务器的测速秒数，默认 5。CLI 分别调用 `tdxman.transport.sync.ping_all(timeout=...)` 和 `ping_mac_all(timeout=...)`，输出 `group`（standard/mac）、`host`、`latency_ms`。测速进度在 stderr，数据表在 stdout 或 `--output`；无法从纯连通延迟证明行情查询可用。需要实际业务请求验证并更新本机优选排名时用 [bestip](bestip.md)。依据：`src/tdxman/cli/cmd_admin.py`、`src/tdxman/transport/sync.py`。
