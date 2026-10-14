"""Local change-detection chain. This provides neither signatures nor immutability."""

import hashlib
import json
import sqlite3
from typing import Any

from deita.models import AuditError
from deita.timeutil import utc_now

GENESIS = "0" * 64


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def entry_digest(sequence: int, timestamp: str, action: str, payload: str, previous: str) -> str:
    material = canonical_json([sequence, timestamp, action, payload, previous])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def verify(connection: sqlite3.Connection) -> dict[str, Any]:
    previous = GENESIS
    count = 0
    for row in connection.execute("SELECT * FROM audit ORDER BY sequence"):
        count += 1
        expected = entry_digest(
            row["sequence"], row["timestamp_utc"], row["action"], row["payload"], previous
        )
        if (
            row["sequence"] != count
            or row["previous_hash"] != previous
            or row["entry_hash"] != expected
            or (count == 1 and row["action"] != "case_created")
        ):
            raise AuditError(f"Audit chain verification failed at entry {count}.")
        previous = expected
    if not count:
        raise AuditError("Audit chain is empty; the creation record is missing.")
    return {"valid": True, "entries": count, "head_sha256": previous}


def append(connection: sqlite3.Connection, action: str, payload: dict[str, Any]) -> None:
    row = connection.execute(
        "SELECT sequence, entry_hash FROM audit ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    sequence = row["sequence"] + 1 if row else 1
    previous = row["entry_hash"] if row else GENESIS
    timestamp = utc_now()
    serialized = canonical_json(payload)
    digest = entry_digest(sequence, timestamp, action, serialized, previous)
    connection.execute(
        "INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?)",
        (sequence, timestamp, action, serialized, previous, digest),
    )
