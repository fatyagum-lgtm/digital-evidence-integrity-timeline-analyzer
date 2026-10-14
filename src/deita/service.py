"""Application operations; source access is strictly read-only."""

import os
from pathlib import Path
from typing import Any

from deita.models import DeitaError
from deita.paths import absolute_path, assert_disjoint, open_directory
from deita.reports import comparison_report, snapshot_report
from deita.scanner import Scanner, identity
from deita.storage import Store
from deita.timeutil import utc_now


def create_snapshot(store: Store, source_path: str | Path) -> dict[str, Any]:
    source = absolute_path(source_path)
    assert_disjoint(store.path, source)
    with open_directory(source) as source_fd, store.transaction():
        root_info = os.fstat(source_fd)
        store.bind_source(source, root_info)
        started = utc_now()
        scan = Scanner(source_fd, identity(os.fstat(store.descriptor))).run()
        # Detect replacement of the root path, not just changes inside its open descriptor.
        with open_directory(source) as current_fd:
            if identity(os.fstat(current_fd)) != identity(root_info):
                raise DeitaError(
                    "Source root was replaced during traversal; no snapshot was saved."
                )
        snapshot_id = store.save_snapshot(scan, started)
    return snapshot_report(store, snapshot_id)


def create_comparison(store: Store, before_id: int, after_id: int) -> dict[str, Any]:
    if before_id >= after_id:
        raise DeitaError(
            "Use two distinct snapshot IDs in acquisition order: "
            "--before must be less than --after."
        )
    with store.transaction():
        return store.save_comparison(comparison_report(store, before_id, after_id))
