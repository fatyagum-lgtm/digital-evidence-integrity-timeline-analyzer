"""Argument parsing and exit-code policy for the deita command."""

import argparse
import json
import sqlite3
import sys
from typing import Any

from deita import __version__, audit
from deita.exporting import export_report
from deita.models import AuditError, DeitaError
from deita.paths import error_code
from deita.reports import snapshot_report, timeline_report
from deita.service import create_comparison, create_snapshot
from deita.storage import initialize, open_case


def positive_id(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected a positive integer ID.") from error
    if not 1 <= number <= 9_223_372_036_854_775_807:
        raise argparse.ArgumentTypeError("IDs must be positive signed 64-bit integers.")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="deita",
        description="Offline file-integrity snapshots and metadata timelines (educational).",
        epilog="Exit codes: 0 success; 1 comparison differences; 2 usage/operation error; "
        "3 incomplete observations; 4 invalid audit chain; 130 interrupted. "
        "JSON is written to stdout; diagnostics go to stderr.",
    )
    root.add_argument("--version", action="version", version=f"deita {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    def case_option(command: argparse.ArgumentParser, operation: str) -> None:
        command.add_argument(
            "--case", required=True, help="Local case directory (never the source)."
        )
        command.set_defaults(operation=operation)

    case = commands.add_parser("case", help="Create a new local case.")
    init = case.add_subparsers(required=True).add_parser(
        "init", help="Refuses existing directories."
    )
    case_option(init, "init")
    init.add_argument("--case-id", required=True, help="Public report identifier, e.g. DEMO-001.")
    init.add_argument("--notes", default="", help="Private local notes; omitted from all reports.")

    snapshots = commands.add_parser("snapshot", help="Create, list or inspect snapshots.")
    snapshot_commands = snapshots.add_subparsers(required=True)
    create = snapshot_commands.add_parser(
        "create", help="Read a source recursively without following links."
    )
    case_option(create, "snapshot_create")
    create.add_argument(
        "--source", required=True, help="Source directory; must be disjoint from the case."
    )
    listing = snapshot_commands.add_parser("list", help="Find snapshot IDs and completeness flags.")
    case_option(listing, "snapshot_list")
    show = snapshot_commands.add_parser(
        "show", help="Show a snapshot report, including errors and exclusions."
    )
    case_option(show, "snapshot_show")
    show.add_argument(
        "--id", type=positive_id, required=True, help="Snapshot ID from snapshot list."
    )

    compare = commands.add_parser(
        "compare", help="Compare and save results for two snapshots in one case."
    )
    case_option(compare, "compare")
    compare.add_argument("--before", type=positive_id, required=True, help="Earlier snapshot ID.")
    compare.add_argument("--after", type=positive_id, required=True, help="Later snapshot ID.")

    comparisons = commands.add_parser("comparison", help="Find saved comparison IDs.")
    listing = comparisons.add_subparsers(required=True).add_parser("list")
    case_option(listing, "comparison_list")

    timeline = commands.add_parser("timeline", help="Show recorded file metadata in UTC order.")
    case_option(timeline, "timeline")
    timeline.add_argument("--snapshot", type=positive_id, required=True, help="Snapshot ID.")

    export = commands.add_parser("export", help="Export JSON/CSV to a NEW file in CASE/exports/.")
    case_option(export, "export")
    export.add_argument("--kind", choices=("snapshot", "comparison", "timeline"), required=True)
    export.add_argument(
        "--id",
        type=positive_id,
        required=True,
        help="Comparison ID for comparison; snapshot ID otherwise.",
    )
    export.add_argument("--format", choices=("json", "csv"), required=True)
    export.add_argument(
        "--name", required=True, help="New plain filename, e.g. comparison.json (no directories)."
    )

    audit_command = commands.add_parser(
        "audit", help="Verify local journal links (not authenticity)."
    )
    verify = audit_command.add_subparsers(required=True).add_parser("verify")
    case_option(verify, "audit_verify")
    return root


def dispatch(args: argparse.Namespace) -> tuple[Any, int]:
    if args.operation == "init":
        return initialize(args.case, args.case_id, args.notes), 0
    with open_case(args.case) as store:
        if args.operation == "snapshot_list":
            return {"case_id": store.info["case_id"], "snapshots": store.list_snapshots()}, 0
        if args.operation == "comparison_list":
            return {"case_id": store.info["case_id"], "comparisons": store.list_comparisons()}, 0
        if args.operation == "audit_verify":
            return audit.verify(store.connection), 0
        if args.operation == "snapshot_create":
            report = create_snapshot(store, args.source)
        elif args.operation == "snapshot_show":
            report = snapshot_report(store, args.id)
        elif args.operation == "compare":
            report = create_comparison(store, args.before, args.after)
        elif args.operation == "timeline":
            report = timeline_report(store, args.snapshot)
        elif args.operation == "export":
            loaders = {"snapshot": snapshot_report, "timeline": timeline_report}
            report = (
                store.comparison(args.id)
                if args.kind == "comparison"
                else loaders[args.kind](store, args.id)
            )
            result = export_report(store, report, args.format, args.name)
            return {**result, "complete": report["complete"]}, 0 if report["complete"] else 3
        else:
            raise DeitaError("Unknown operation.")
        if not report["complete"]:
            return report, 3
        if args.operation == "compare" and any(
            report["summary"][key] for key in ("modified", "added", "removed")
        ):
            return report, 1
        return report, 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result, code = dispatch(args)
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return code
    except AuditError as error:
        print(f"deita: {error}", file=sys.stderr)
        return 4
    except DeitaError as error:
        print(f"deita: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(
            f"deita: filesystem operation failed ({error_code(error)}). Check permissions, "
            "existing paths and symlink components; no links are followed.",
            file=sys.stderr,
        )
        return 2
    except sqlite3.Error as error:
        print(
            f"deita: database operation failed ({type(error).__name__}). "
            "Check the case database and whether another process holds its lock.",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print("deita: interrupted; active database transaction rolled back.", file=sys.stderr)
        return 130
