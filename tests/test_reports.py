import csv
import errno
import io
import json
import os
import re
from urllib.parse import unquote_to_bytes

import pytest

from deita import scanner
from deita.exporting import csv_report, export_report
from deita.models import DeitaError
from deita.paths import relative_path
from deita.reports import snapshot_report, timeline_report
from deita.service import create_comparison, create_snapshot
from deita.storage import open_case
from deita.timeutil import ns_to_utc


def test_full_comparison_and_metadata_only_change(workspace):
    case, source = workspace
    for name in ("stable", "changed", "removed", "metadata-only"):
        (source / name).write_bytes(b"before")
    with open_case(case) as store:
        first = create_snapshot(store, source)
        (source / "changed").write_bytes(b"after!")
        (source / "removed").unlink()
        (source / "added").write_bytes(b"new")
        os.utime(
            source / "metadata-only", ns=(1_700_000_000_000_000_000, 1_700_000_000_123_456_789)
        )
        second = create_snapshot(store, source)
        report = create_comparison(store, first["snapshot_id"], second["snapshot_id"])
        assert report["summary"] == {
            "unchanged": 2,
            "modified": 1,
            "added": 1,
            "removed": 1,
            "unreadable": 0,
        }
        assert store.comparison(report["comparison_id"]) == report
    rows = {row["path"]: row for row in report["rows"]}
    assert rows["metadata-only"]["status"] == "unchanged"
    assert rows["metadata-only"]["metadata_changed"] is True
    assert rows["changed"]["before_sha256"] != rows["changed"]["after_sha256"]


def test_read_errors_take_precedence_over_content_comparison(workspace, monkeypatch):
    case, source = workspace
    (source / "file").write_text("same bytes")
    with open_case(case) as store:
        create_snapshot(store, source)

        def fail(_fd, _size):
            raise OSError(errno.EIO, "failure")

        monkeypatch.setattr(scanner, "hash_descriptor", fail)
        create_snapshot(store, source)
        report = create_comparison(store, 1, 2)
    assert report["rows"][0]["status"] == "unreadable"
    assert report["rows"][0]["reason"] == "after:EIO"
    assert report["complete"] is False


def test_unreadable_subtree_is_never_claimed_removed_or_added(workspace, monkeypatch):
    case, source = workspace
    blocked = source / "blocked"
    blocked.mkdir()
    (blocked / "known.txt").write_text("data")
    (source / "healthy").write_text("same")
    blocked_identity = scanner.identity(blocked.stat())
    original = os.listdir

    def denied(descriptor):
        if (
            isinstance(descriptor, int)
            and scanner.identity(os.fstat(descriptor)) == blocked_identity
        ):
            raise PermissionError(errno.EACCES, "denied")
        return original(descriptor)

    with open_case(case) as store:
        create_snapshot(store, source)
        with monkeypatch.context() as patch:
            patch.setattr(os, "listdir", denied)
            patch.setattr(os, "supports_fd", os.supports_fd | {denied})
            partial = create_snapshot(store, source)
        create_snapshot(store, source)
        disappearance = create_comparison(store, 1, 2)
        reappearance = create_comparison(store, 2, 3)
    assert partial["complete"] is False
    for report in (disappearance, reappearance):
        assert report["summary"]["unreadable"] == 1
        assert report["summary"]["removed"] == report["summary"]["added"] == 0
        assert report["summary"]["unchanged"] == 1
        assert report["issues"][0]["code"] == "unreadable_directory"


def test_replacement_with_excluded_link_is_not_definitive_removal(workspace):
    case, source = workspace
    file = source / "known"
    file.write_bytes(b"data")
    with open_case(case) as store:
        create_snapshot(store, source)
        file.unlink()
        file.symlink_to(source.parent / "absent")
        create_snapshot(store, source)
        report = create_comparison(store, 1, 2)
    assert report["rows"][0]["status"] == "unreadable"
    assert "symlink_skipped" in report["rows"][0]["reason"]


def test_paths_round_trip_without_absolute_paths_or_csv_formulas(workspace):
    case, source = workspace
    names = [
        "normal.txt",
        "snowman-☃.txt",
        "line\nbreak",
        "a\\b",
        "=SUM(1,1)",
        "100%",
        "with space",
    ]
    for name in names:
        (source / name).write_text("synthetic")
    if os.name == "posix":
        raw_name = b"nonutf8-\xff"
        fd = os.open(
            os.fsencode(source) + b"/" + raw_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        os.close(fd)
        names.append(os.fsdecode(raw_name))
    with open_case(case) as store:
        report = create_snapshot(store, source)
    decoded = {os.fsdecode(unquote_to_bytes(row["path"])) for row in report["rows"]}
    assert decoded == set(names)
    serialized = json.dumps(report)
    assert str(source) not in serialized and "private-user" not in serialized
    assert all(
        not row["path"].startswith(("/", "=", "@", "+", "-"))
        for row in report["rows"]
        if row["path"] != "normal.txt"
    )
    assert json.loads(serialized) == report
    csv_rows = list(csv.DictReader(io.StringIO(csv_report(report))))
    assert len([row for row in csv_rows if row["row_type"] == "file"]) == len(names)


@pytest.mark.parametrize("parts", [("..",), ("/absolute",), (".",), ("a/b",), ("",), ("\x00",)])
def test_unsafe_path_components_rejected(parts):
    with pytest.raises(DeitaError):
        relative_path(parts)


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "1970-01-01T00:00:00.000000000Z"),
        (-1, "1969-12-31T23:59:59.999999999Z"),
        (1_704_067_200_123_456_789, "2024-01-01T00:00:00.123456789Z"),
    ],
)
def test_nanosecond_utc_no_float_rounding(value, expected):
    assert ns_to_utc(value) == expected


def test_timeline_semantics_sorting_and_available_fields(workspace):
    case, source = workspace
    (source / "one").write_bytes(b"1")
    (source / "two").write_bytes(b"2")
    with open_case(case) as store:
        snapshot = create_snapshot(store, source)
        report = timeline_report(store, snapshot["snapshot_id"])
    events = report["rows"]
    assert len(events) >= 6
    assert events == sorted(
        events, key=lambda row: (row["timestamp_ns"], row["path"], row["event_type"])
    )
    for event in events:
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}Z", event["timestamp_utc"])
        if event["event_type"] == "ctime":
            assert event["meaning"] == "metadata_change_posix"
    for row in snapshot["rows"]:
        assert (row["birthtime_ns"] is None) == (row["birthtime_utc"] is None)
        assert (row["birthtime_ns"] is None) == (row["birthtime_precision"] is None)


@pytest.mark.parametrize("kind", ["snapshot", "comparison", "timeline"])
@pytest.mark.parametrize("file_format", ["json", "csv"])
def test_all_report_exports_with_metadata_and_issues(workspace, kind, file_format):
    case, source = workspace
    (source / "file").write_text("synthetic")
    (source / "link").symlink_to("missing")
    with open_case(case) as store:
        create_snapshot(store, source)
        create_snapshot(store, source)
        comparison = create_comparison(store, 1, 2)
        report = {
            "comparison": comparison,
            "snapshot": snapshot_report(store, 2),
            "timeline": timeline_report(store, 2),
        }[kind]
        export_report(store, report, file_format, f"result.{file_format}")
    path = case / "exports" / f"result.{file_format}"
    content = path.read_text()
    assert str(source) not in content and str(case) not in content and "private-user" not in content
    if file_format == "json":
        assert json.loads(content) == report
    else:
        rows = list(csv.DictReader(io.StringIO(content)))
        assert rows[0]["row_type"] == "summary"
        assert rows[0]["case_id"] == "TEST-001"
        assert any(row["row_type"] == "issue" and row["code"] == "symlink_skipped" for row in rows)
        assert "path" in rows[0]


def test_empty_export_keeps_header_and_summary(workspace):
    case, source = workspace
    with open_case(case) as store:
        create_snapshot(store, source)
        rows = list(csv.DictReader(io.StringIO(csv_report(timeline_report(store, 1)))))
    assert len(rows) == 1 and rows[0]["row_type"] == "summary"


def test_export_refuses_overwrite_traversal_and_symlink(workspace):
    case, source = workspace
    target = source / "do-not-touch"
    target.write_text("evidence")
    with open_case(case) as store:
        report = create_snapshot(store, source)
        export_report(store, report, "json", "new.json")
        original = (case / "exports" / "new.json").read_bytes()
        with pytest.raises(DeitaError, match="exists"):
            export_report(store, report, "json", "new.json")
        assert (case / "exports" / "new.json").read_bytes() == original
        for name in ("../escape.json", "/tmp/escape.json", "nested/file.json", "bad.csv"):
            with pytest.raises(DeitaError):
                export_report(store, report, "json", name)
        (case / "exports" / "link.json").symlink_to(target)
        with pytest.raises(DeitaError):
            export_report(store, report, "json", "link.json")
    assert target.read_text() == "evidence"


def test_export_directory_symlink_refused(workspace):
    case, source = workspace
    with open_case(case) as store:
        report = create_snapshot(store, source)
        (case / "exports").rmdir()
        (case / "exports").symlink_to(source, target_is_directory=True)
        with pytest.raises(OSError):
            export_report(store, report, "json", "bad.json")
    assert not (source / "bad.json").exists()
