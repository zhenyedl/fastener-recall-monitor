"""CLI used by a local scheduler or a review-capable agent."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys

from . import core
from .jdy import load_config
from .source import fetch_snapshot, load_snapshot, make_snapshot
from .sync import synchronize


def summary(batch):
    return {"id": batch["id"], "date": batch["date"], "status": batch["status"],
            "new_count": len(batch["new"]), "known_count": batch["known_count"],
            "new": batch["new"] if batch["status"] == "pending" else []}


def current(base):
    path = base / "batches" / "current.json"
    return core.read(path) if path.exists() else None


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def run_command(args, base):
    if args.command == "init":
        core.initialize(base, core.read(args.baseline) if args.baseline else None)
        return {"initialized": True}, 0
    if args.command == "status":
        batch = current(base)
        return summary(batch) if batch else {"status": "no_batch"}, 0
    if args.command == "review":
        batch = core.review(core.read(base / "batches" / "current.json"), core.read(args.input))
        core.save(base / "batches" / "current.json", batch)
        return summary(batch), 0
    if args.command in ("fetch", "snapshot"):
        if args.command == "snapshot":
            if not args.confirm_complete:
                raise ValueError("Confirm all relevant pages are included with --confirm-complete")
            snapshot = make_snapshot(core.read(args.input), args.url, args.since)
        else:
            snapshot = fetch_snapshot(args.url, args.since)
        core.save(args.output, snapshot)
        return {"candidate_count": len(snapshot["items"]), "snapshot_saved": True}, 0
    if args.command == "sync":
        config = None if args.dry_run else load_config(args.config)
        return synchronize(base, config, dry_run=args.dry_run), 0
    if args.command in ("prepare", "run"):
        batch = current(base)
        if batch and batch["status"] != "complete":
            if args.command == "prepare" or batch["status"] == "pending":
                return {**summary(batch), "resumed": True}, 3 if batch["status"] == "pending" else 0
            config = load_config(args.config)
            result = synchronize(base, config)
            # The current day's listing still has to be checked after a retry.
            return {**result, "resumed": True, "fresh_check_required": True}, 4
        if args.command == "prepare" or args.snapshot:
            snapshot = load_snapshot(args.snapshot)
        else:
            if not args.url or not args.since:
                raise ValueError("Provide --snapshot, or --url and --since")
            snapshot = fetch_snapshot(args.url, args.since)
        batch = core.prepare(snapshot["items"], base)
        batch["source_check"] = {key: value for key, value in snapshot.items() if key != "items"}
        core.save(base / "batches" / "current.json", batch)
        core.save(base / "snapshots" / (batch["id"] + ".json"), snapshot)
        if batch["status"] == "pending":
            return summary(batch), 3
        if args.command == "prepare":
            return summary(batch), 0
        return synchronize(base, load_config(args.config)), 0
    raise ValueError("Unknown command")


def parser():
    p = argparse.ArgumentParser(description="Incremental SAMR recall monitor")
    p.add_argument("--home", default=os.environ.get("RECALL_HOME", ".recall"), help="Private state directory")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Initialize empty state; optional reviewed baseline")
    init.add_argument("--baseline", help="Previously reviewed metadata ARRAY, never current unreviewed data")
    sub.add_parser("status")
    for name in ("fetch", "snapshot"):
        cmd = sub.add_parser(name, help="Collect or attest complete official listing metadata")
        cmd.add_argument("--url", action="append", required=True, help="Official listing page; repeat for each page in order")
        cmd.add_argument("--since", required=True, help="Required overlap date, YYYY-MM-DD")
        cmd.add_argument("--output", required=True)
        if name == "snapshot":
            cmd.add_argument("--input", required=True, help="Metadata ARRAY obtained via official browser/list")
            cmd.add_argument("--confirm-complete", action="store_true")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--snapshot", required=True)
    review = sub.add_parser("review")
    review.add_argument("--input", required=True)
    sync = sub.add_parser("sync")
    sync.add_argument("--config", default="config.json")
    sync.add_argument("--dry-run", action="store_true")
    run = sub.add_parser("run", help="Check, diff, stop for review, or synchronize a reviewed/empty batch")
    run.add_argument("--snapshot")
    run.add_argument("--url", action="append")
    run.add_argument("--since")
    run.add_argument("--config", default="config.json")
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    base = Path(args.home).resolve()
    with core.lock(base):
        try:
            result, code = run_command(args, base)
        except (ValueError, KeyError, OSError, RuntimeError, TypeError) as exc:
            # Do not dump config, HTTP responses, credentials, or source file paths.
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
            result, code = {"status": "error", "error": message}, 1
        log = base / "logs" / (core.today() + ".jsonl")
        log.parent.mkdir(parents=True, exist_ok=True)
        # Keep logs compact: full candidate/review details are already persisted.
        safe = {key: value for key, value in result.items() if key not in ("new", "detail")}
        with log.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"time": dt.datetime.now(core.CST).isoformat(),
                                  "command": args.command, "exit_code": code, **safe},
                                 ensure_ascii=False) + "\n")
        emit(result)
        return code
