# Actual synthetic demonstration outputs

These files were produced by the CLI using the exact demonstration command block
in the top-level README. The fixture generator is `scripts/demo_data.py`. There
are no real user files or case databases in this directory.

| File | Origin |
| --- | --- |
| `comparison.json`, `comparison.csv` | Saved comparison 1: snapshot 1 → snapshot 2 |
| `timeline.json`, `timeline.csv` | Recorded metadata from snapshot 2 |
| `snapshot.json` | Complete file observations from snapshot 2 |
| `audit-verification.json` | `deita audit verify` after the five report exports |

The comparison has two unchanged paths and one each added, modified, and removed.
The journal has nine entries: case creation, two snapshots, one comparison, and
five exports. Read-only list, timeline, and verification commands do not append
entries. The recorded head is an example result, not an external trusted anchor;
the synthetic case database is intentionally not distributed.

The file bytes in the demo are deterministic; acquisition timestamps, ctime,
atime, birth time availability, and the audit head depend on the execution
environment. These samples were generated on Linux, where this run did not expose
birth time through `os.stat`. Different outputs on macOS are expected.

CSV includes the summary row as well as typed data rows. JSON retains raw integer
nanosecond values; preserve integer precision when processing it.
