# Digital Evidence Integrity & Timeline Analyzer

**An offline Python CLI for documenting file collections, checking SHA-256 integrity
between observations, and building timelines from recorded filesystem metadata.**

> **Educational portfolio project.** This is not a certified forensic tool, a legal
> chain-of-custody system, or a replacement for established forensic procedures.
> Work on authorized working copies. File timestamps are observations, not proof
> of when a real-world event occurred.

When a folder changes between two examinations, a list of filenames is not enough
to explain what changed. DEITA saves a baseline, reads the collection again, and
reports content differences alongside uncertainty and exclusions. All application
operations are local: no telemetry, uploads, external APIs, or network requests.

## Implemented

- Local cases with a public case ID and private optional notes.
- Recursive snapshots with bounded, streaming SHA-256, size, and available timestamps.
- Stable ordering and portable, reversible representations of relative filenames.
- SQLite storage for cases, snapshots, file observations, scan issues, comparison
  results, and a hash-linked action journal.
- `unchanged`, `modified`, `added`, `removed`, and `unreadable` comparisons.
- Explicit directory-coverage errors: an inaccessible subtree is not mistaken for
  deleted files. Files that change during hashing have no accepted digest.
- UTC metadata timelines and JSON/CSV exports, including warnings and scan issues.
- No-follow traversal, source/case separation, new-file-only exports, and audit
  chain verification with clearly bounded guarantees.
- Synthetic integration tests, Ruff lint/format configuration, and GitHub Actions CI.

## Requirements and installation

Python **3.11+** and a POSIX environment with descriptor-relative operations and
`O_NOFOLLOW`: Linux or macOS. Native Windows is deliberately refused; use a Linux
environment such as WSL. WSL itself has not been validated here. Mount and filesystem
behavior still matters. See [validation](docs/VALIDATION.md) for what was actually run.

The runtime uses **only Python's standard library**: `argparse` for the CLI,
`hashlib` for hashing, `sqlite3` for storage, and `json`/`csv` for reports. There is no
CLI-formatting dependency. `pytest`, `ruff`, and `build` are development-only tools.
Installation may download build/development packages; running DEITA does not.

From the repository directory, on Linux or macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
deita --help
```

For ordinary use without development tools: `python -m pip install .`.
`python -m deita` is equivalent to the installed `deita` command.

## A complete synthetic demonstration

Run the following **from the repository root after installation**. `demo-work`
must not already exist. To repeat, use a new directory name throughout the block.
The small helper deliberately changes only the synthetic collection it creates;
the analyzer never changes those source files.

<!-- demo:start -->
```bash
python scripts/demo_data.py create demo-work
deita case init --case demo-work/case --case-id DEMO-001 --notes "Synthetic demonstration only"
deita snapshot create --case demo-work/case --source demo-work/source
python scripts/demo_data.py change demo-work
deita snapshot create --case demo-work/case --source demo-work/source
deita snapshot list --case demo-work/case
deita compare --case demo-work/case --before 1 --after 2 || test "$?" -eq 1
deita comparison list --case demo-work/case
deita timeline --case demo-work/case --snapshot 2
deita export --case demo-work/case --kind comparison --id 1 --format json --name comparison.json
deita export --case demo-work/case --kind comparison --id 1 --format csv --name comparison.csv
deita export --case demo-work/case --kind timeline --id 2 --format json --name timeline.json
deita export --case demo-work/case --kind timeline --id 2 --format csv --name timeline.csv
deita export --case demo-work/case --kind snapshot --id 2 --format json --name snapshot.json
deita audit verify --case demo-work/case
```
<!-- demo:end -->

The comparison intentionally returns **1** when content differences are found.
The `|| test "$?" -eq 1` handles that expected result while preserving other errors.
An incomplete comparison returns **3**, even if some differences are also known.

The saved comparison from this demo has this summary:

```json
{
  "unchanged": 2,
  "modified": 1,
  "added": 1,
  "removed": 1,
  "unreadable": 0
}
```

| Relative path | Comparison result |
| --- | --- |
| `added.txt` | `added` |
| `keep.txt` | `unchanged` |
| `nested/session.log` | `unchanged` |
| `notes.txt` | `modified` |
| `removed.txt` | `removed` |

Actual generated examples are in [examples](examples/README.md). Timestamps and
audit heads depend on the run and are not expected to be identical on your machine.
The test suite extracts and executes the demonstration block above verbatim.

### Choosing IDs and output files

Each case has its own integer snapshot sequence. The `snapshot_id` returned by
`snapshot create` is the identifier to use. `snapshot list` shows IDs, UTC start/end
times, completeness, and file/issue counts. In a **new** case the first two successful
snapshots are `1` and `2`; do not assume these numbers in an existing case.
Partial snapshots are saved and receive IDs too. Aborted operations do not.

`compare --before N --after M` requires two existing IDs in that same case, with
`N < M`. It returns a separate **comparison ID** and saves the result.
`comparison list` finds saved comparison IDs. For `export --kind comparison`,
`--id` means a **comparison ID**. For `--kind snapshot` or `--kind timeline`, it
means a **snapshot ID**. `timeline --snapshot N` uses that one recorded snapshot,
not a new scan of the source.

Exports are confined to `CASE/exports/`. `--name` accepts a plain ASCII filename
ending in `.json` or `.csv`; directory components are refused. Existing files and
links are never overwritten. Each successful export prints its relative output
location, byte count, and SHA-256. Exporting incomplete observations still writes
the report, but returns `3` so automation can notice the uncertainty.

## Report semantics

### Comparison results

| Status | Meaning |
| --- | --- |
| `unchanged` | Same relative path and same successfully observed SHA-256. Metadata may differ. |
| `modified` | Same relative path, different successfully observed SHA-256. |
| `added` | A file is observed only in the later snapshot, with no coverage issue affecting the path. |
| `removed` | A file is observed only in the earlier snapshot, with no coverage issue affecting the path. |
| `unreadable` | A read failed, an observation was unstable, or an exclusion/coverage issue prevents a reliable comparison. Consult `reason` and `issues`. |

Rows include `path`, `status`, before/after SHA-256, sizes, observation statuses,
`metadata_changed`, and an optional `reason`. Metadata-only changes remain
`unchanged`; `metadata_changed` includes atime and may reflect the previous scan's
own access. Renames appear as removal plus addition; no rename inference is made.

Snapshot file status is `ok`, `unreadable`, or `unstable`; the latter two have a
null SHA-256. Issues separately describe skipped links, special files, unreadable
directories, and directory changes. An issue with path `.` covers the source root.
`complete: true` means no detected errors **within the supported scan scope**;
informational exclusions still appear in `issues`. It never means every filesystem
object was acquired. An excluded path that affects a comparison becomes `unreadable`.

### Timeline fields

Each event contains `path`, `event_type`, `timestamp_utc`, `timestamp_ns`, `meaning`,
`precision`, `file_status`, `size`, `sha256`, and `error`. Events are ordered by epoch
nanoseconds, then path, then event type. The recorded times of files that could not
be hashed can still appear, clearly marked with their file status.

| Event | Interpretation |
| --- | --- |
| `mtime` | Filesystem-reported content modification time. |
| `ctime` | POSIX metadata/inode-change time, **not file creation time**. |
| `atime` | Access time observed **before the file's hash read**; earlier reads may already have affected it. |
| `birthtime` | Creation time only when exposed by the platform; otherwise null in snapshots and absent from timelines. |

UTC text has a fixed form such as `2024-01-01T00:00:00.123456789Z`. Integer
nanoseconds are retained without float conversion for atime, mtime, and ctime.
Nine displayed digits do **not** imply nine digits of filesystem accuracy. Birth
time uses `st_birthtime_ns` if available; a `st_birthtime` float fallback is tagged
`float_seconds` and has only its original precision. JSON consumers must preserve
large integers; JavaScript `Number` cannot represent every epoch nanosecond exactly.

### JSON, CSV, and path privacy

JSON has a versioned envelope, `summary`, `rows`, and `issues`. CSV has a common
header and a `row_type`: `summary`, `file`, `difference`, `event`, or `issue`.
The summary row carries `report_metadata_json`, so even an empty CSV retains its
scope and completeness. Issue rows are included, not silently dropped. Empty CSV
cells represent nulls. Formula-like text cells receive a leading apostrophe.

Paths are **percent-encoded filesystem bytes per segment**, joined by `/`.
For example, `notes final.txt` becomes `notes%20final.txt`, `%` becomes `%25`, and a
literal filename backslash becomes `%5C`. This also preserves undecodable POSIX
names and prevents newlines from changing row structure. Decode individual segments
with `urllib.parse.unquote_to_bytes` for exact bytes. Never treat report paths as
trusted output destinations or automatically open decoded paths.

Reports omit the absolute source path, case location, private notes, raw exception
messages, and link targets. The SQLite database **does** contain the private source
path and notes; do not publish case directories. Relative filenames and your chosen
public case ID can themselves be sensitive, so review reports before sharing.

## Safety boundaries and limitations

- Sources are opened read-only. The tool does not write, rename, delete, move,
  chmod, or restore timestamps on source files. **Reading can update atime** and
  filesystem housekeeping. Use appropriate working copies and external read-only
  mounts/write blockers when required; DEITA does not provide those controls.
- Source and case must be disjoint in both directions. A recognized `.deita-case`
  workspace anywhere inside a source aborts the entire snapshot. The marker name
  is reserved. A case binds to one source path and root device/inode identity;
  using another source requires another case. There is no relocation/rebind feature.
- All supplied path components, including ancestors, must be real directories;
  symlinks are refused. On macOS `/tmp` and `/var` may be aliases: use the physical
  path (for example `/private/tmp`) or a directory under your real home path.
  Parent `..` components are rejected. Source links, including broken links and
  loops, are recorded but not followed. Their targets are never recorded.
- Multiply linked regular files, special files, different-device filesystem
  boundaries, and repeated directory identities are explicitly excluded. Depth
  beyond 128 nested directories produces a partial snapshot. A same-device bind
  mount is not always identifiable; mount topology is outside the trust boundary.
- File fingerprints are checked before/after hashing, and directory changes are
  flagged. This detects many races but cannot freeze an active filesystem or
  guarantee detection of every change. Use a stable copy; this is not an atomic
  filesystem snapshot or a tool for scanning a hostile live system.
- The case directory and its parents must remain private and under your control.
  SQLite uses path-based opens; descriptor-safe source traversal is **not** a
  sandbox against an attacker replacing the case workspace, its parents, database
  sidecars, or mount points while commands run. Run as a regular user, not root.
- SHA-256 comparison concerns observed file content, not origin, ownership,
  authenticity, hidden streams, deleted data, extended attributes, or legal validity.
  Disk-image acquisition, recovery, filesystem journal parsing, ACL collection,
  and malware execution/analysis are outside scope. File contents are not parsed.
- The audit journal detects inconsistent links/entries. It is **not immutable**,
  signed, or externally anchored. Someone who can rewrite the database can rebuild
  its chain. Deleting a valid suffix or restoring an older database cannot generally
  be detected without an external trusted head. Verification does not authenticate
  snapshot tables, compare exported files, or log read-only commands and failed attempts.
- Database writes are transactional. An interrupted snapshot is rolled back.
  An OS/process crash during export may leave a complete or partial file without
  an audit entry: SQLite and filesystem creation cannot share one transaction.
- File content memory is bounded to 1 MiB per read; metadata, issues, comparisons,
  and reports are held in memory. This version targets modest collections rather
  than millions of files. A blocked filesystem read can still block; no timeout is provided.

See [SECURITY.md](SECURITY.md) and [architecture](docs/ARCHITECTURE.md) for details.

## Architecture

| Module | Responsibility |
| --- | --- |
| `cli.py` | Argument parsing, JSON stdout, diagnostics, exit codes |
| `paths.py`, `scanner.py` | Path policy, descriptor traversal, bounded hashing, race checks |
| `models.py`, `timeutil.py` | Typed observations and precise UTC formatting |
| `storage.py`, `service.py` | SQLite transactions and case/snapshot/comparison operations |
| `reports.py`, `exporting.py` | Conservative comparison, timeline generation, safe exports |
| `audit.py` | Canonical journal serialization and linked-hash verification |
| `tests/` | Synthetic unit/integration tests including the README command sequence |

SQLite schema version 1 uses foreign keys and tables for case metadata, snapshots,
file observations, issues, comparisons, and audit entries. Structured observations
are JSON within indexed snapshot/path rows; comparisons are saved JSON reports.
A single write transaction spans a scan and its audit entry, preventing partial
snapshot commits. Concurrent writers wait up to five seconds; busy errors are
reported rather than retrying indefinitely. See the architecture document for the
reasoning behind these choices.

## Exit codes and development checks

| Code | Meaning |
| --- | --- |
| `0` | Operation succeeded; comparison has no content differences. |
| `1` | Completed comparison found added, removed, or modified files. |
| `2` | Invalid arguments, missing IDs, unsafe path, filesystem or database error. |
| `3` | Partial/inconclusive observations; inspect the emitted report. |
| `4` | Audit chain failed verification; write operations also refuse that chain. |
| `130` | Interrupted operation. |

Diagnostics use stderr; successful or partial structured output uses stdout.
Run `deita COMMAND --help` for command-specific arguments.

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m build
```

CI is configured to run on clean Linux and macOS checkouts using Python
3.11, 3.12, and 3.13, including the actual README demo through pytest. Configuration
is not evidence of a completed hosted run. No coverage percentage or CI badge is claimed.

## Roadmap — not implemented

- A versioned case relocation workflow with explicit provenance of the rebind.
- Incremental/streamed metadata and report processing for very large collections.
- Optional externally stored audit checkpoints and verification against those anchors.
- Additional artifact adapters, each with independent validation and a defined scope.

## References

- [Python 3.11 `os`: descriptor-relative access, stat fields and platform behavior](https://docs.python.org/3.11/library/os.html)
- [Python 3.11 `sqlite3`: transactions and connection behavior](https://docs.python.org/3.11/library/sqlite3.html)
- [Python 3.11 `hashlib`](https://docs.python.org/3.11/library/hashlib.html)

MIT licensed. Author: Fatima Humbatova. This repository records a learning and
engineering project; it makes no claim of professional forensic validation.

## Evidence Handling Notes

- Work on copies of evidence rather than altering original files.
- Use SHA-256 comparisons to help detect changes between recorded observations.
- Treat filesystem timestamps as investigative leads, not standalone proof of when an event occurred.
- Document the collection environment, analysis steps, and any limitations when reporting findings.
