"""Export to new files inside the case only; preserve incomplete-scan warnings."""

import csv
import hashlib
import io
import json
import os
import re
from typing import Any

from deita import audit
from deita.models import DeitaError
from deita.storage import Store

ROW_FIELDS = {
    "comparison": [
        "path",
        "status",
        "before_status",
        "after_status",
        "before_sha256",
        "after_sha256",
        "before_size",
        "after_size",
        "metadata_changed",
        "reason",
    ],
    "timeline": [
        "path",
        "event_type",
        "timestamp_ns",
        "timestamp_utc",
        "meaning",
        "file_status",
        "sha256",
        "size",
        "error",
        "precision",
    ],
    "snapshot": [
        "path",
        "status",
        "size",
        "sha256",
        "mtime_ns",
        "ctime_ns",
        "atime_ns",
        "birthtime_ns",
        "mtime_utc",
        "ctime_utc",
        "atime_utc",
        "birthtime_utc",
        "birthtime_precision",
        "error",
    ],
}


def csv_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


def csv_report(report: dict[str, Any]) -> str:
    stream = io.StringIO(newline="")
    columns = ["row_type", "case_id", "report_kind", "report_id", "complete"]
    columns += ROW_FIELDS[report["kind"]]
    columns += ["issue_snapshot_id", "code", "severity", "scope", "detail", "report_metadata_json"]
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    common = {
        "case_id": report["case_id"],
        "report_kind": report["kind"],
        "report_id": report.get("comparison_id", report.get("snapshot_id")),
        "complete": report["complete"],
    }

    def write(row: dict[str, Any]) -> None:
        writer.writerow({key: csv_cell(value) for key, value in {**common, **row}.items()})

    meta = {key: value for key, value in report.items() if key not in {"rows", "issues"}}
    write({"row_type": "summary", "report_metadata_json": audit.canonical_json(meta)})
    row_type = {"comparison": "difference", "timeline": "event", "snapshot": "file"}[report["kind"]]
    for row in report["rows"]:
        write({"row_type": row_type, **row})
    for issue in report["issues"]:
        copied = dict(issue)
        copied["issue_snapshot_id"] = copied.pop("snapshot_id", report.get("snapshot_id"))
        write({"row_type": "issue", **copied})
    return stream.getvalue()


def export_report(
    store: Store, report: dict[str, Any], file_format: str, name: str
) -> dict[str, Any]:
    if file_format not in {"json", "csv"}:
        raise DeitaError("Export format must be json or csv.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", name) or not name.endswith(
        "." + file_format
    ):
        raise DeitaError(
            "Use a plain ASCII filename ending in the chosen format, without directories."
        )
    content = (
        json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
        if file_format == "json"
        else csv_report(report)
    )
    data = content.encode("utf-8")
    # Filesystem creation and SQLite commit cannot be one atomic operation. On a normal
    # failure remove only our new output. A process crash may leave an unaudited export.
    with store.exports_directory() as directory:
        created = False
        try:
            with store.transaction():
                try:
                    descriptor = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=directory,
                    )
                except FileExistsError as error:
                    raise DeitaError(
                        "Export already exists; choose a new --name. Nothing was overwritten."
                    ) from error
                created = True
                with os.fdopen(descriptor, "wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                audit.append(
                    store.connection,
                    "report_exported",
                    {
                        "kind": report["kind"],
                        "format": file_format,
                        "filename": name,
                        "sha256": hashlib.sha256(data).hexdigest(),
                    },
                )
        except BaseException:
            if created:
                os.unlink(name, dir_fd=directory)
            raise
    return {
        "export": "exports/" + name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
