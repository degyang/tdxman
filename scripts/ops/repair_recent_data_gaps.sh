#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_dir/.local/uv-cache}"
exec uv run python "$project_dir/scripts/ops/repair_recent_data_gaps.py" "$@"
