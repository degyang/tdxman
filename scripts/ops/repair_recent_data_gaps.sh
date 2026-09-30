#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_dir"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_dir/.local/uv-cache}"
if [[ -x "$project_dir/.venv/bin/python" ]]; then
  exec "$project_dir/.venv/bin/python" "$project_dir/scripts/ops/repair_recent_data_gaps.py" "$@"
fi
exec uv run --frozen python "$project_dir/scripts/ops/repair_recent_data_gaps.py" "$@"
