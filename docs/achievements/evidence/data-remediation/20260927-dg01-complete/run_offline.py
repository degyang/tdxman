"""DG-01 offline acceptance: explicit lab roots and the verified audit guard."""

import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

LAB = Path("/home/ubuntu/aspool-labs/20260927-dg01/task_c544312049ac")
GUARD = Path(__file__).parent.parent / "20260927-orca-review/run_offline.py"
spec = importlib.util.spec_from_file_location("review_guard", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

if __name__ == "__main__":
    os.environ["XMTDX_LIVE"] = "0"
    sys.addaudithook(guard.audit)
    LAB.mkdir(parents=True, exist_ok=True)
    args = sys.argv[1:] or [
        "tests/",
        "-q",
        "--basetemp=" + str(LAB / ("full-offline-" + uuid4().hex)),
    ]
    raise SystemExit(pytest.main(args))
