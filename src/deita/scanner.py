"""Read ordinary files through directory descriptors; never follow symlinks."""

import hashlib
import os
import stat
from dataclasses import replace
from decimal import Decimal

from deita.models import DeitaError, FileRecord, ScanIssue, ScanResult
from deita.paths import contains_case_marker, directory_flags, error_code, relative_path

CHUNK_SIZE = 1024 * 1024
MAX_DEPTH = 128


class ChangedDuringRead(Exception):
    """The observed object changed while it was being examined."""


def identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def fingerprint(info: os.stat_result) -> tuple[int, ...]:
    # atime is intentionally omitted: the tool's own reads may update it.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def metadata(path: str, info: os.stat_result) -> FileRecord:
    birth_ns = getattr(info, "st_birthtime_ns", None)
    precision = "nanoseconds" if birth_ns is not None else None
    if birth_ns is None and hasattr(info, "st_birthtime"):
        birth_ns = int(Decimal(str(info.st_birthtime)) * 1_000_000_000)
        precision = "float_seconds"
    return FileRecord(
        path=path,
        status="ok",
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
        atime_ns=info.st_atime_ns,
        birthtime_ns=birth_ns,
        birthtime_precision=precision,
    )


def hash_descriptor(descriptor: int, expected_size: int) -> str:
    digest = hashlib.sha256()
    remaining = expected_size
    while remaining:
        chunk = os.read(descriptor, min(CHUNK_SIZE, remaining))
        if not chunk:
            raise ChangedDuringRead
        digest.update(chunk)
        remaining -= len(chunk)
    # One bounded extra read detects growth, including growth of an empty file.
    if os.read(descriptor, 1):
        raise ChangedDuringRead
    return digest.hexdigest()


def read_file(parent_fd: int, name: str, path: str, observed: os.stat_result) -> FileRecord:
    record = metadata(path, observed)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or fingerprint(before) != fingerprint(observed):
            raise ChangedDuringRead
        record = metadata(path, before)
        digest = hash_descriptor(descriptor, before.st_size)
        after = os.fstat(descriptor)
        named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(
            named_after
        ):
            raise ChangedDuringRead
        return replace(record, sha256=digest)
    except ChangedDuringRead:
        return replace(record, status="unstable", error="changed_during_read", sha256=None)
    except OSError as error:
        return replace(record, status="unreadable", error=error_code(error), sha256=None)
    finally:
        if descriptor is not None:
            os.close(descriptor)


class Scanner:
    def __init__(self, root_fd: int, forbidden_identity: tuple[int, int]) -> None:
        self.root_fd = root_fd
        self.root_device = os.fstat(root_fd).st_dev
        self.forbidden_identity = forbidden_identity
        self.seen: set[tuple[int, int]] = set()
        self.result = ScanResult()

    def issue(
        self,
        parts: tuple[str, ...],
        code: str,
        detail: str,
        *,
        error: bool = False,
        subtree: bool = False,
    ) -> None:
        self.result.issues.append(
            ScanIssue(
                relative_path(parts) or ".",
                code,
                "error" if error else "info",
                "subtree" if subtree else "entry",
                detail,
            )
        )

    def run(self) -> ScanResult:
        self.walk(self.root_fd, ())
        self.result.files.sort(key=lambda item: item.path)
        self.result.issues.sort(key=lambda item: (item.path, item.code))
        return self.result

    def walk(self, descriptor: int, parts: tuple[str, ...]) -> None:
        before = os.fstat(descriptor)
        if identity(before) == self.forbidden_identity or contains_case_marker(descriptor):
            raise DeitaError("A case workspace was found inside the source; no snapshot was saved.")
        if identity(before) in self.seen:
            self.issue(
                parts, "directory_alias", "Repeated directory identity excluded.", subtree=True
            )
            return
        self.seen.add(identity(before))
        try:
            names = sorted(os.listdir(descriptor))
        except OSError as error:
            self.issue(parts, "unreadable_directory", error_code(error), error=True, subtree=True)
            return
        for name in names:
            self.visit(descriptor, name, (*parts, name))
        after = os.fstat(descriptor)
        if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            self.issue(
                parts,
                "directory_changed",
                "Directory membership may have changed during traversal.",
                error=True,
                subtree=True,
            )

    def visit(self, parent_fd: int, name: str, parts: tuple[str, ...]) -> None:
        try:
            observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            self.issue(parts, "stat_failed", error_code(error), error=True, subtree=True)
            return
        if stat.S_ISLNK(observed.st_mode):
            self.issue(parts, "symlink_skipped", "Symbolic link not followed.", subtree=True)
        elif observed.st_dev != self.root_device:
            self.issue(parts, "device_boundary", "Different filesystem excluded.", subtree=True)
        elif stat.S_ISDIR(observed.st_mode):
            self.visit_directory(parent_fd, name, parts, observed)
        elif stat.S_ISREG(observed.st_mode):
            if observed.st_nlink != 1:
                self.issue(parts, "hardlink_skipped", "Multiply linked file excluded.")
            else:
                self.result.files.append(read_file(parent_fd, name, relative_path(parts), observed))
        else:
            self.issue(parts, "special_file_skipped", "Not an ordinary file or directory.")

    def visit_directory(
        self, parent_fd: int, name: str, parts: tuple[str, ...], observed: os.stat_result
    ) -> None:
        if len(parts) > MAX_DEPTH:
            self.issue(
                parts, "depth_limit", "Directory depth limit exceeded.", error=True, subtree=True
            )
            return
        child: int | None = None
        try:
            child = os.open(name, directory_flags(), dir_fd=parent_fd)
            if identity(os.fstat(child)) != identity(observed):
                raise ChangedDuringRead
            self.walk(child, parts)
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if identity(current) != identity(observed) or not stat.S_ISDIR(current.st_mode):
                raise ChangedDuringRead
        except ChangedDuringRead:
            self.issue(
                parts, "directory_replaced", "Directory identity changed.", error=True, subtree=True
            )
        except OSError as error:
            self.issue(parts, "unreadable_directory", error_code(error), error=True, subtree=True)
        finally:
            if child is not None:
                os.close(child)
