#!/usr/bin/env bash
# Stable cron/tmux entry point for the public daily aspool pipeline.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_dir"
export TZ="${TZ:-Asia/Shanghai}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_dir/.local/uv-cache}"

echo "daily pipeline: starting; project=$project_dir"

# The repository venv is the normal operational environment.  Reuse it directly
# so a stale uv cache lock cannot make a scheduled run wait without any output.
if [[ -x "$project_dir/.venv/bin/python" ]]; then
  exec "$project_dir/.venv/bin/python" \
    "$project_dir/scripts/ops/run_daily_data_pipeline.py" "$@"
fi

echo "daily pipeline: .venv unavailable; bootstrapping with uv"
exec uv run --frozen python "$project_dir/scripts/ops/run_daily_data_pipeline.py" "$@"
