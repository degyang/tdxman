#!/usr/bin/env bash
# Copy a closed development dataset; run inside tmux with writers stopped.
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
remote_host=${1:-jakartavps}
remote_project=${2:-/home/ubuntu/Services/tdxman}
case "$remote_host" in -*|*[!a-zA-Z0-9_.@-]*) echo 'Invalid SSH host' >&2; exit 2;; esac
case "$remote_project" in /*) ;; *) echo 'Remote project must be absolute' >&2; exit 2;; esac
case "$remote_project" in *[!a-zA-Z0-9_./-]*|*/../*) echo 'Unsafe remote path' >&2; exit 2;; esac
manifest="$repo_root/.local/reports/full-copy-manifest.json"
evidence="$repo_root/docs/evidence/sqlite-migration-assessment/20260928-derived-result.json"
[[ -f "$manifest" && -f "$evidence" ]]
[[ ! -s "$repo_root/data/stocks.sqlite-wal" ]]
[[ ! -f "$repo_root/data/catalog.duckdb.wal" ]]
if fuser "$repo_root/data/stocks.sqlite" "$repo_root/data/catalog.duckdb" >/dev/null 2>&1; then
    echo 'Close database readers and writers before copying' >&2; exit 1
fi
"$repo_root/.venv/bin/python" - "$manifest" "$repo_root/.local/reports/full-copy-files.txt" <<'PY'
import json, sys
from pathlib import PurePosixPath, Path
value=json.loads(Path(sys.argv[1]).read_text())
paths=[]
for item in value['files']:
    path=PurePosixPath(item['path'])
    if path.is_absolute() or '..' in path.parts or '\n' in str(path):
        raise ValueError('Unsafe manifest path')
    paths.append(str(path))
Path(sys.argv[2]).write_text('\n'.join(paths)+'\n')
PY
ssh -o BatchMode=yes "$remote_host" "test -d '$remote_project' && mkdir -p '$remote_project/.local/receive/data' '$remote_project/.local/reports' && df -h '$remote_project'"
# --whole-file intentionally transfers complete files, not a live database delta.
# The receiver directory is isolated; do not point a consumer at it before verification.
rsync -a --whole-file --partial --info=progress2 \
    --files-from="$repo_root/.local/reports/full-copy-files.txt" \
    -e 'ssh -o BatchMode=yes' "$repo_root/data/" \
    "$remote_host:$remote_project/.local/receive/data/"
rsync -a -e 'ssh -o BatchMode=yes' "$manifest" "$evidence" \
    "$remote_host:$remote_project/.local/reports/"
ssh -o BatchMode=yes "$remote_host" \
    "cd '$remote_project' && .venv/bin/python scripts/ops/verify_data_replica.py --root .local/receive/data --manifest .local/reports/full-copy-manifest.json --integrity-evidence .local/reports/20260928-derived-result.json --report .local/reports/full-copy-verification.json"
echo 'Full replica verified. Install only after closing receiver database users.'
