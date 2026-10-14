"""UTC formatting without converting nanosecond epoch integers to floats."""

import time
from datetime import UTC, datetime, timedelta

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def ns_to_utc(value: int) -> str:
    seconds, nanos = divmod(value, 1_000_000_000)
    instant = EPOCH + timedelta(seconds=seconds)
    return f"{instant.isoformat(timespec='seconds')[:19]}.{nanos:09d}Z"


def utc_now() -> str:
    return ns_to_utc(time.time_ns())
