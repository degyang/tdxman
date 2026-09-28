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
[[ ! -s "$repo_root/data/indices.sqlite-wal" ]]
[[ ! -s "$repo_root/data/etfs.sqlite-wal" ]]
[[ ! -f "$repo_root/data/catalog.duckdb.wal" ]]
database_paths=("$repo_root/data/stocks.sqlite" "$repo_root/data/catalog.duckdb")
if [[ -f "$repo_root/data/indices.sqlite" ]]; then
    database_paths+=("$repo_root/data/indices.sqlite")
fi
if [[ -f "$repo_root/data/etfs.sqlite" ]]; then
    database_paths+=("$repo_root/data/etfs.sqlite")
fi
if fuser "${database_paths[@]}" >/dev/null 2>&1; then
    echo 'Close database readers and writers before copying' >&2; exit 1
fi
"$repo_root/.venv/bin/python" - "$manifest" "$repo_root/.local/reports/full-copy-files.txt" "$repo_root/data" <<'PY'
import json, sys
from pathlib import PurePosixPath, Path
value=json.loads(Path(sys.argv[1]).read_text())
paths=[]
seen=set()
for item in value['files']:
    path=PurePosixPath(item['path'])
    if path.is_absolute() or '..' in path.parts or '\n' in str(path):
        raise ValueError('Unsafe manifest path')
    if str(path) in seen:
        raise ValueError(f'Duplicate manifest path: {path}')
    seen.add(str(path))
    paths.append(str(path))
if (Path(sys.argv[3]) / 'indices.sqlite').exists() and 'indices.sqlite' not in seen:
    raise ValueError('Manifest omits the migrated indices.sqlite; rebuild it before copying')
if (Path(sys.argv[3]) / 'etfs.sqlite').exists() and 'etfs.sqlite' not in seen:
    raise ValueError('Manifest omits etfs.sqlite; rebuild it before copying')
if sum(item['bytes'] for item in value['files']) != value['bytes']:
    raise ValueError('Manifest total bytes do not match file sizes')
Path(sys.argv[2]).write_text('\n'.join(paths)+'\n')
PY
ssh -o BatchMode=yes "$remote_host" "test -d '$remote_project' && mkdir -p '$remote_project/.local/receive/data' '$remote_project/.local/reports' && df -h '$remote_project'"
# --whole-file intentionally transfers complete files, not a live database delta.
# The receiver directory is isolated; do not point a consumer at it before verification.
rsync -a --whole-file --partial --compress --compress-choice=zstd --compress-level=3 --info=progress2 \
    --files-from="$repo_root/.local/reports/full-copy-files.txt" \
    -e 'ssh -o BatchMode=yes' "$repo_root/data/" \
    "$remote_host:$remote_project/.local/receive/data/"
rsync -a -e 'ssh -o BatchMode=yes' "$manifest" "$evidence" \
    "$remote_host:$remote_project/.local/reports/"
ssh -o BatchMode=yes "$remote_host" \
    "cd '$remote_project' && .venv/bin/python scripts/ops/verify_data_replica.py --root .local/receive/data --manifest .local/reports/full-copy-manifest.json --integrity-evidence .local/reports/20260928-derived-result.json --report .local/reports/full-copy-verification.json"
echo 'Full replica verified. Install only after closing receiver database users.'
