"""DG04 immutable dependency planning and bounded resumable republication."""

import argparse
import json
from datetime import date
from pathlib import Path

from aspool.remediation import execute, plan
from aspool.remediation_repair import execute_repair, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("plan")
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--start", type=date.fromisoformat, required=True)
    create.add_argument("--end", type=date.fromisoformat, required=True)
    create.add_argument("--recovery-manifest", type=Path, required=True)
    create.add_argument("--plan", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--state", type=Path, required=True)
    run.add_argument("--max-days", type=int, required=True)
    run.add_argument("--approval", type=Path)
    repair = commands.add_parser("prepare-repair")
    repair.add_argument("--lab-root", type=Path, required=True)
    repair.add_argument("--root", type=Path, required=True)
    repair.add_argument("--payload", type=Path, required=True)
    repair.add_argument("--recovery-manifest", type=Path, required=True)
    repair.add_argument("--plan", type=Path, required=True)
    replay = commands.add_parser("repair")
    replay.add_argument("--plan", type=Path, required=True)
    replay.add_argument("--state", type=Path, required=True)
    replay.add_argument("--max-symbols", type=int, required=True)
    replay.add_argument("--approval", type=Path)
    args = parser.parse_args()
    if args.command == "plan":
        result = plan(args.root, args.start, args.end, args.recovery_manifest, args.plan)
        print(json.dumps({key: result[key] for key in ("plan_id", "source", "rule")}))
        print(f"sessions={len(result['dates'])}; {result['dates'][0]}..{result['dates'][-1]}")
    elif args.command == "prepare-repair":
        result = prepare(args.lab_root, args.root, args.payload, args.recovery_manifest, args.plan)
        print(json.dumps(dict(plan_id=result["plan_id"], steps=len(result["steps"]))))
    elif args.command == "repair":
        result = execute_repair(
            args.plan, args.state, max_symbols=args.max_symbols, approval=args.approval
        )
        print(json.dumps(dict(next=result["next"], pending=result["pending"])))
    else:
        result = execute(args.plan, args.state, max_days=args.max_days, approval=args.approval)
        print(
            json.dumps(
                dict(
                    next=result["next"],
                    pending=result["pending"],
                    completed=len(result["completed"]),
                )
            )
        )


if __name__ == "__main__":
    main()
