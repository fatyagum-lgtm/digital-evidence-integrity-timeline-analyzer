import errno
import hashlib
import os
from pathlib import Path

import pytest

from deita import scanner
from deita.models import DeitaError
from deita.service import create_snapshot
from deita.storage import initialize, open_case


def take(case: Path, source: Path) -> dict:
    with open_case(case) as store:
        return create_snapshot(store, source)


@pytest.mark.parametrize("payload", [b"", b"abc", b"\x00\xff" * 1_100_000])
def test_exact_streaming_sha256_and_repeated_content(workspace, payload, monkeypatch):
    case, source = workspace
    (source / "a.bin").write_bytes(payload)
    (source / "b.bin").write_bytes(payload)
    lengths = []
    original = os.read

    def bounded_read(fd, count):
        lengths.append(count)
        return original(fd, count)

    monkeypatch.setattr(scanner.os, "read", bounded_read)
    result = take(case, source)
    assert result["complete"] is True
    assert [row["sha256"] for row in result["rows"]] == [hashlib.sha256(payload).hexdigest()] * 2
    assert max(lengths) <= scanner.CHUNK_SIZE


def test_empty_directory_is_complete(workspace):
    case, source = workspace
    result = take(case, source)
    assert result["rows"] == [] and result["issues"] == []
    assert result["complete"] is True


def test_stable_order_and_source_content_metadata_unchanged(workspace):
    case, source = workspace
    (source / "nested").mkdir()
    for name in ("z.txt", "a.txt", "nested/b.txt"):
        (source / name).write_bytes(name.encode())
    before = {
        p.relative_to(source): (p.read_bytes(), p.stat()) for p in source.rglob("*") if p.is_file()
    }
    result = take(case, source)
    assert [row["path"] for row in result["rows"]] == ["a.txt", "nested/b.txt", "z.txt"]
    for name, (content, info) in before.items():
        current = (source / name).stat()
        assert (source / name).read_bytes() == content
        assert (current.st_ino, current.st_mtime_ns, current.st_ctime_ns, current.st_size) == (
            info.st_ino,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_size,
        )


def test_read_failure_is_recorded_without_private_path(workspace, monkeypatch):
    case, source = workspace
    (source / "denied.txt").write_text("private")

    def denied(_descriptor, _expected_size):
        raise PermissionError(errno.EACCES, "do not persist", "/Users/private-user/secret")

    monkeypatch.setattr(scanner, "hash_descriptor", denied)
    report = take(case, source)
    row = report["rows"][0]
    assert row["status"] == "unreadable" and row["error"] == "EACCES"
    assert row["sha256"] is None and report["complete"] is False
    assert "private-user" not in str(report)


def test_changed_file_discards_digest(workspace, monkeypatch):
    case, source = workspace
    target = source / "file.txt"
    target.write_bytes(b"old")
    original = scanner.hash_descriptor

    def mutate(fd, size):
        digest = original(fd, size)
        target.write_bytes(b"changed")
        return digest

    monkeypatch.setattr(scanner, "hash_descriptor", mutate)
    row = take(case, source)["rows"][0]
    assert row["status"] == "unstable"
    assert row["error"] == "changed_during_read" and row["sha256"] is None


@pytest.mark.parametrize("actual,expected", [(b"abc", 2), (b"a", 2), (b"a", 0)])
def test_hash_rejects_growth_or_truncation(tmp_path, actual, expected):
    file = tmp_path / "file"
    file.write_bytes(actual)
    with file.open("rb") as handle, pytest.raises(scanner.ChangedDuringRead):
        scanner.hash_descriptor(handle.fileno(), expected)


def test_symlinks_broken_links_loops_and_special_files_are_not_opened(workspace):
    case, source = workspace
    outside = source.parent / "outside"
    outside.mkdir()
    (outside / "secret").write_text("not evidence")
    (source / "file-link").symlink_to(outside / "secret")
    (source / "dir-link").symlink_to(outside, target_is_directory=True)
    (source / "broken").symlink_to(outside / "absent")
    (source / "loop").symlink_to(source, target_is_directory=True)
    os.mkfifo(source / "pipe")
    report = take(case, source)
    assert report["rows"] == []
    assert [issue["code"] for issue in report["issues"]].count("symlink_skipped") == 4
    assert any(issue["code"] == "special_file_skipped" for issue in report["issues"])


def test_hard_links_are_explicitly_excluded(workspace):
    case, source = workspace
    outside = source.parent / "outside"
    outside.write_bytes(b"same inode")
    os.link(outside, source / "link")
    report = take(case, source)
    assert not report["rows"]
    assert report["issues"][0]["code"] == "hardlink_skipped"


def test_file_replaced_by_symlink_between_stat_and_open(workspace, monkeypatch):
    case, source = workspace
    file = source / "file"
    file.write_bytes(b"initial")
    outside = source.parent / "secret"
    outside.write_bytes(b"must not hash")
    original = scanner.read_file

    def swap(parent_fd, name, path, observed):
        file.unlink()
        file.symlink_to(outside)
        return original(parent_fd, name, path, observed)

    monkeypatch.setattr(scanner, "read_file", swap)
    row = take(case, source)["rows"][0]
    assert row["status"] == "unreadable" and row["sha256"] is None


def test_directory_replaced_by_symlink_is_not_traversed(workspace, monkeypatch):
    case, source = workspace
    directory = source / "folder"
    directory.mkdir()
    outside = source.parent / "outside"
    outside.mkdir()
    (outside / "secret").write_text("must not read")
    original = scanner.Scanner.visit_directory

    def swap(self, parent_fd, name, parts, observed):
        directory.rmdir()
        directory.symlink_to(outside, target_is_directory=True)
        return original(self, parent_fd, name, parts, observed)

    monkeypatch.setattr(scanner.Scanner, "visit_directory", swap)
    report = take(case, source)
    assert report["rows"] == [] and report["complete"] is False


@pytest.mark.parametrize("relation", ["same", "case_inside", "source_inside"])
def test_overlapping_case_and_source_refused(tmp_path, relation):
    root = tmp_path.resolve()
    if relation == "case_inside":
        source, case = root, root / "case"
    elif relation == "source_inside":
        case, source = root / "case", root / "case" / "source"
    else:
        case = source = root / "case"
    initialize(case, "SAFE")
    if relation == "source_inside":
        source.mkdir()
    with open_case(case) as store, pytest.raises(DeitaError, match="disjoint"):
        create_snapshot(store, source)
    with open_case(case) as store:
        assert store.list_snapshots() == []


def test_other_case_inside_source_aborts_snapshot(workspace):
    case, source = workspace
    initialize(source / "another-case", "OTHER")
    with open_case(case) as store, pytest.raises(DeitaError, match="workspace"):
        create_snapshot(store, source)
    with open_case(case) as store:
        assert store.list_snapshots() == [] and store.info["source_path"] is None


def test_root_or_ancestor_symlink_refused(workspace):
    case, source = workspace
    alias = source.parent / "alias"
    alias.symlink_to(source, target_is_directory=True)
    (source / "nested").mkdir()
    for path in (alias, alias / "nested"):
        with pytest.raises(OSError):
            take(case, path)


def test_case_cannot_switch_sources(workspace):
    case, source = workspace
    take(case, source)
    other = source.parent / "other"
    other.mkdir()
    with pytest.raises(DeitaError, match="bound"):
        take(case, other)


def test_depth_limit_is_visible(workspace, monkeypatch):
    case, source = workspace
    (source / "one" / "two").mkdir(parents=True)
    monkeypatch.setattr(scanner, "MAX_DEPTH", 1)
    report = take(case, source)
    assert report["complete"] is False
    assert report["issues"][0]["code"] == "depth_limit"
