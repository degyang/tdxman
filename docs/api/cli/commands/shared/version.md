# `tdxman version`

```bash
tdxman version
```

无参数，输出纯文本 `tdxman <version>`，不是 JSON。根命令的 `tdxman --version` 也返回版本。CLI 从 `tdxman.__version__` 读取当前安装包版本；版本与仓库源码不符时，先确认实际执行的是哪个安装环境，再参考当前命令 `--help`。依据：`src/tdxman/cli/cmd_admin.py`、`src/tdxman/__init__.py`、`pyproject.toml`。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
