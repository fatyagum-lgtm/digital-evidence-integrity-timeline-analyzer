"""SQLite persistence. A case is a private, user-controlled local workspace."""

import json
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from deita import __version__, audit
from deita.models import DeitaError, FileRecord, ScanIssue, ScanResult
from deita.paths import CASE_MARKER, absolute_path, directory_flags, open_directory
from deita.timeutil import utc_now

DB_NAME = "case.sqlite3"
SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE case_info (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    case_id TEXT NOT NULL, notes TEXT NOT NULL, created_utc TEXT NOT NULL,
    source_path TEXT, source_device INTEGER, source_inode INTEGER
);
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY, started_utc TEXT NOT NULL, finished_utc TEXT NOT NULL,
    complete INTEGER NOT NULL CHECK(complete IN (0, 1))
);
CREATE TABLE files (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    path TEXT NOT NULL, record_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, path)
);
CREATE TABLE issues (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    ordinal INTEGER NOT NULL, issue_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, ordinal)
);
CREATE TABLE comparisons (
    id INTEGER PRIMARY KEY,
    before_id INTEGER NOT NULL REFERENCES snapshots(id),
    after_id INTEGER NOT NULL REFERENCES snapshots(id),
    created_utc TEXT NOT NULL, report_json TEXT NOT NULL
);
CREATE TABLE audit (
    sequence INTEGER PRIMARY KEY, timestamp_utc TEXT NOT NULL,
    action TEXT NOT NULL, payload TEXT NOT NULL,
    previous_hash TEXT NOT NULL, entry_hash TEXT NOT NULL
);
PRAGMA user_version = 1;
"""


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=5
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def initialize(case_path: str | Path, case_id: str, notes: str = "") -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", case_id):
        raise DeitaError(
            "Case ID must be 1-64 ASCII letters, digits, '.', '_' or '-', starting alphanumeric."
        )
    path = absolute_path(case_path)
    with open_directory(path.parent) as parent_fd:
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError as error:
            raise DeitaError(
                "Case directory already exists. Choose a new directory; nothing was replaced."
            ) from error
    with open_directory(path) as descriptor:
        for name in (CASE_MARKER, DB_NAME):
            created = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor
            )
            os.close(created)
        os.mkdir("exports", mode=0o700, dir_fd=descriptor)
    connection = connect(path / DB_NAME)
    timestamp = utc_now()
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
        connection.execute(
            "INSERT INTO case_info VALUES (1, ?, ?, ?, NULL, NULL, NULL)",
            (case_id, notes, timestamp),
        )
        audit.append(connection, "case_created", {"case_id": case_id, "tool_version": __version__})
        connection.commit()
    finally:
        connection.close()
    return {"case_id": case_id, "created_utc": timestamp, "schema_version": SCHEMA_VERSION}


class Store:
    def __init__(self, path: Path, descriptor: int, connection: sqlite3.Connection) -> None:
        self.path = path
        self.descriptor = descriptor
        self.connection = connection

    @property
    def info(self) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM case_info WHERE singleton = 1").fetchone()
        if row is None:
            raise DeitaError("Case metadata is missing.")
        return dict(row)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            audit.verify(self.connection)
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def bind_source(self, source: Path, info: os.stat_result) -> None:
        saved = self.info
        if saved["source_path"] is None:
            self.connection.execute(
                "UPDATE case_info SET source_path = ?, source_device = ?, source_inode = ? "
                "WHERE singleton = 1",
                (str(source), info.st_dev, info.st_ino),
            )
        elif (saved["source_path"], saved["source_device"], saved["source_inode"]) != (
            str(source),
            info.st_dev,
            info.st_ino,
        ):
            raise DeitaError(
                "This case is bound to another source path or directory identity. "
                "Create a new case."
            )

    def save_snapshot(self, scan: ScanResult, started: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO snapshots(started_utc, finished_utc, complete) VALUES (?, ?, ?)",
            (started, utc_now(), int(scan.complete)),
        )
        snapshot_id = int(cursor.lastrowid or 0)
        self.connection.executemany(
            "INSERT INTO files VALUES (?, ?, ?)",
            [(snapshot_id, file.path, audit.canonical_json(asdict(file))) for file in scan.files],
        )
        self.connection.executemany(
            "INSERT INTO issues VALUES (?, ?, ?)",
            [
                (snapshot_id, index, audit.canonical_json(asdict(issue)))
                for index, issue in enumerate(scan.issues)
            ],
        )
        audit.append(
            self.connection,
            "snapshot_created",
            {
                "snapshot_id": snapshot_id,
                "file_count": len(scan.files),
                "issue_count": len(scan.issues),
                "complete": scan.complete,
            },
        )
        return snapshot_id

    def snapshot(
        self, snapshot_id: int
    ) -> tuple[dict[str, Any], list[FileRecord], list[ScanIssue]]:
        row = self.connection.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            raise DeitaError(
                f"Snapshot {snapshot_id} does not exist; run 'deita snapshot list --case ...'."
            )
        files = [
            FileRecord(**json.loads(item[0]))
            for item in self.connection.execute(
                "SELECT record_json FROM files WHERE snapshot_id = ? ORDER BY path", (snapshot_id,)
            )
        ]
        issues = [
            ScanIssue(**json.loads(item[0]))
            for item in self.connection.execute(
                "SELECT issue_json FROM issues WHERE snapshot_id = ? ORDER BY ordinal",
                (snapshot_id,),
            )
        ]
        meta = dict(row)
        meta["complete"] = bool(meta["complete"])
        return meta, files, issues

    def list_snapshots(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
            SELECT s.*, (SELECT COUNT(*) FROM files f WHERE f.snapshot_id = s.id) AS file_count,
            (SELECT COUNT(*) FROM issues i WHERE i.snapshot_id = s.id) AS issue_count
            FROM snapshots s ORDER BY s.id
        """)
        return [{**dict(row), "complete": bool(row["complete"])} for row in rows]

    def save_comparison(self, report: dict[str, Any]) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO comparisons(before_id, after_id, created_utc, report_json) "
            "VALUES (?, ?, ?, ?)",
            (report["before_id"], report["after_id"], report["created_utc"], "{}"),
        )
        comparison_id = int(cursor.lastrowid or 0)
        report["comparison_id"] = comparison_id
        self.connection.execute(
            "UPDATE comparisons SET report_json = ? WHERE id = ?",
            (
                audit.canonical_json(report),
                comparison_id,
            ),
        )
        audit.append(
            self.connection,
            "comparison_created",
            {
                "comparison_id": comparison_id,
                "before_id": report["before_id"],
                "after_id": report["after_id"],
                "summary": report["summary"],
            },
        )
        return report

    def comparison(self, comparison_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT report_json FROM comparisons WHERE id = ?", (comparison_id,)
        ).fetchone()
        if row is None:
            raise DeitaError(
                f"Comparison {comparison_id} does not exist; "
                "run 'deita comparison list --case ...'."
            )
        return json.loads(row[0])

    def list_comparisons(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT id, before_id, after_id, created_utc FROM comparisons ORDER BY id"
            )
        ]

    @contextmanager
    def exports_directory(self) -> Iterator[int]:
        descriptor = os.open("exports", directory_flags(), dir_fd=self.descriptor)
        try:
            yield descriptor
        finally:
            os.close(descriptor)


@contextmanager
def open_case(case_path: str | Path) -> Iterator[Store]:
    path = absolute_path(case_path)
    with open_directory(path) as descriptor:
        for name in (CASE_MARKER, DB_NAME):
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise DeitaError("Case marker/database must be ordinary, singly linked files.")
        connection = connect(path / DB_NAME)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise DeitaError(
                    f"Unsupported database schema {version}; expected {SCHEMA_VERSION}."
                )
            yield Store(path, descriptor, connection)
        finally:
            connection.close()
