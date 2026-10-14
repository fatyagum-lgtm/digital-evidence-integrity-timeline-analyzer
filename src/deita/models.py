"""Serializable records; paths are percent-encoded relative POSIX paths."""

from dataclasses import dataclass, field
from typing import Literal


class DeitaError(Exception):
    """An expected, actionable error suitable for display by the CLI."""


class AuditError(DeitaError):
    """The existing audit chain did not verify."""


@dataclass(frozen=True)
class FileRecord:
    path: str
    status: Literal["ok", "unreadable", "unstable"]
    size: int | None = None
    sha256: str | None = None
    mtime_ns: int | None = None
    ctime_ns: int | None = None
    atime_ns: int | None = None
    birthtime_ns: int | None = None
    birthtime_precision: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ScanIssue:
    path: str
    code: str
    severity: Literal["info", "error"]
    scope: Literal["entry", "subtree"]
    detail: str


@dataclass
class ScanResult:
    files: list[FileRecord] = field(default_factory=list)
    issues: list[ScanIssue] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return all(f.status == "ok" for f in self.files) and not any(
            issue.severity == "error" for issue in self.issues
        )
