"""Path-private reports and conservative comparison semantics."""

from collections import Counter
from dataclasses import asdict
from typing import Any

from deita import __version__
from deita.models import FileRecord, ScanIssue
from deita.storage import Store
from deita.timeutil import ns_to_utc, utc_now

WARNING = "Educational metadata observations, not proof of event time or forensic chain of custody."
TIME_FIELDS = ("mtime", "ctime", "atime", "birthtime")
SEMANTICS = {
    "mtime": "content_modification",
    "ctime": "metadata_change_posix",
    "atime": "access_before_hash_read",
    "birthtime": "creation_platform_dependent",
}


def envelope(store: Store, kind: str) -> dict[str, Any]:
    return {
        "report_schema_version": 1,
        "tool_version": __version__,
        "kind": kind,
        "case_id": store.info["case_id"],
        "path_encoding": "percent-encoded filesystem bytes per segment; '/' separates segments",
        "warning": WARNING,
    }


def file_dict(record: FileRecord) -> dict[str, Any]:
    result = asdict(record)
    for field in TIME_FIELDS:
        value = result[f"{field}_ns"]
        result[f"{field}_utc"] = ns_to_utc(value) if value is not None else None
    return result


def snapshot_report(store: Store, snapshot_id: int) -> dict[str, Any]:
    meta, files, issues = store.snapshot(snapshot_id)
    return {
        **envelope(store, "snapshot"),
        "snapshot_id": meta["id"],
        "started_utc": meta["started_utc"],
        "finished_utc": meta["finished_utc"],
        "complete": meta["complete"],
        "summary": {
            "files": len(files),
            "statuses": dict(Counter(f.status for f in files)),
            "issues": len(issues),
        },
        "rows": [file_dict(file) for file in files],
        "issues": [asdict(issue) for issue in issues],
    }


def affected(path: str, issues: list[ScanIssue]) -> list[str]:
    return sorted(
        {
            issue.code
            for issue in issues
            if (
                path == issue.path
                or (
                    issue.scope == "subtree"
                    and (issue.path == "." or path.startswith(issue.path + "/"))
                )
            )
        }
    )


def compare_files(
    path: str,
    before: FileRecord | None,
    after: FileRecord | None,
    before_issues: list[ScanIssue],
    after_issues: list[ScanIssue],
) -> dict[str, Any]:
    reasons = ["before:" + code for code in affected(path, before_issues)]
    reasons += ["after:" + code for code in affected(path, after_issues)]
    for side, record in (("before", before), ("after", after)):
        if record is not None and (record.status != "ok" or record.sha256 is None):
            reasons.append(f"{side}:{record.error or record.status}")
    if reasons:
        status = "unreadable"
    elif before is None:
        status = "added"
    elif after is None:
        status = "removed"
    elif before.sha256 == after.sha256:
        status = "unchanged"
    else:
        status = "modified"
    return {
        "path": path,
        "status": status,
        "before_status": before.status if before else None,
        "after_status": after.status if after else None,
        "before_sha256": before.sha256 if before else None,
        "after_sha256": after.sha256 if after else None,
        "before_size": before.size if before else None,
        "after_size": after.size if after else None,
        "metadata_changed": (
            any(
                getattr(before, name + "_ns") != getattr(after, name + "_ns")
                for name in TIME_FIELDS
            )
            if before and after
            else None
        ),
        "reason": ";".join(reasons) or None,
    }


def comparison_report(store: Store, before_id: int, after_id: int) -> dict[str, Any]:
    before_meta, before, before_issues = store.snapshot(before_id)
    after_meta, after, after_issues = store.snapshot(after_id)
    before_map = {file.path: file for file in before}
    after_map = {file.path: file for file in after}
    rows = [
        compare_files(path, before_map.get(path), after_map.get(path), before_issues, after_issues)
        for path in sorted(before_map.keys() | after_map.keys())
    ]
    counts = Counter(row["status"] for row in rows)
    summary = {
        key: counts[key] for key in ("unchanged", "modified", "added", "removed", "unreadable")
    }
    return {
        **envelope(store, "comparison"),
        "created_utc": utc_now(),
        "before_id": before_id,
        "after_id": after_id,
        "complete": before_meta["complete"] and after_meta["complete"] and not counts["unreadable"],
        "summary": summary,
        "rows": rows,
        "issues": [
            {"snapshot_id": snapshot_id, **asdict(issue)}
            for snapshot_id, issues in ((before_id, before_issues), (after_id, after_issues))
            for issue in issues
        ],
    }


def timeline_report(store: Store, snapshot_id: int) -> dict[str, Any]:
    meta, files, issues = store.snapshot(snapshot_id)
    events = []
    for file in files:
        for field in TIME_FIELDS:
            timestamp = getattr(file, field + "_ns")
            if timestamp is not None:
                events.append(
                    {
                        "path": file.path,
                        "event_type": field,
                        "timestamp_ns": timestamp,
                        "timestamp_utc": ns_to_utc(timestamp),
                        "meaning": SEMANTICS[field],
                        "file_status": file.status,
                        "sha256": file.sha256,
                        "size": file.size,
                        "error": file.error,
                        "precision": file.birthtime_precision
                        if field == "birthtime"
                        else "nanoseconds",
                    }
                )
    events.sort(key=lambda event: (event["timestamp_ns"], event["path"], event["event_type"]))
    return {
        **envelope(store, "timeline"),
        "snapshot_id": snapshot_id,
        "started_utc": meta["started_utc"],
        "finished_utc": meta["finished_utc"],
        "complete": meta["complete"],
        "summary": {"events": len(events), "issues": len(issues)},
        "rows": events,
        "issues": [asdict(issue) for issue in issues],
    }
