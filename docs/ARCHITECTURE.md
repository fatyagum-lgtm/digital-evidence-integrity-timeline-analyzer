# Architecture and design decisions

## Scope

A case follows one stable directory of working copies over time. A snapshot is a
sequence of observations between `started_utc` and `finished_utc`, not a filesystem
freeze. SHA-256 answers whether successfully read bytes differ at the same relative
path. The timeline orders recorded metadata, not investigator assertions about events.

The CLI, scanner, reporting code, and SQLite layer are separate so policy can be
tested without formatting or shell dependencies. The standard library is enough
for the requested feature set. No service, GUI, parser plugins, or network layer
is present.

## Source traversal

1. Normalize paths lexically, refusing `..`. Open each directory component from `/`
   with `O_DIRECTORY | O_NOFOLLOW`; never call `resolve()` on an evidence path.
2. Refuse overlapping case/source trees. Open the source root and bind the case
   to its local absolute path, device, and inode. The path is private database data.
3. Enumerate directory names in a stable order. Use `stat(..., follow_symlinks=False)`.
   Record all policy exclusions as issues. Abort if a case marker or the current
   case directory identity is encountered. Track visited directory identities.
4. Open regular, singly linked files relative to the parent descriptor with
   `O_RDONLY | O_NOFOLLOW | O_NONBLOCK`. Check the open object against the object
   just observed. `O_NONBLOCK` prevents an accidental FIFO substitution from
   hanging at open; the subsequent regular-file check prevents hashing it.
5. Read at most the initial file size in chunks of at most 1 MiB, followed by one
   byte to detect growth. Early EOF or an extra byte invalidates the digest.
6. Compare device, inode, mode, link count, size, mtime and ctime before/after,
   and re-check the pathname without following links. Do not include atime in
   this fingerprint because the tool's own reads may update it.
7. Check directory identity and membership timestamps, and re-open the root to
   detect root replacement. Mark affected scopes uncertain or abort, as appropriate.

The checks reduce races without claiming a race-free view of the whole tree.
File changes after their individual observation may be missed. A directory may
be renamed while its descriptor remains usable. Atime changes are not undone.
Different-device mounts are excluded; same-device bind mounts can evade that
boundary. Case parents and SQLite's path-based access remain trusted.

## Storage and failure atomicity

Schema version 1 has these tables:

| Table | Contents and key |
| --- | --- |
| `case_info` | One public case ID, notes, creation time, private source binding |
| `snapshots` | Integer ID, UTC start/end, completeness flag |
| `files` | `(snapshot_id, path)` and the complete typed observation as JSON |
| `issues` | `(snapshot_id, ordinal)` and a structured issue as JSON |
| `comparisons` | Integer ID, before/after foreign keys, creation time, saved JSON report |
| `audit` | Ordered action records, previous hash, entry hash |

JSON-in-row storage keeps nullable platform-specific observations simple while
retaining a unique indexed path per snapshot. It trades SQL analytics flexibility
for a small, explicit schema. Snapshots and comparisons are append-only through
the application, but the underlying database is editable by its owner.

Connections enable foreign keys and disable trusted schema behavior. Explicit
`BEGIN IMMEDIATE`, commit, and rollback give the same transaction policy on
Python 3.11+. SQLite's normal rollback journal and full synchronization are used.
The five-second lock timeout is surfaced as an operation error. A write operation
first verifies the existing audit chain within its transaction. A snapshot, its
source binding, all observations/issues, and its action entry commit together.
SIGINT rolls back an active transaction. Read-only commands are not action entries.

Exports use a descriptor for the existing `exports` directory and create-only
`O_EXCL` output. File contents are flushed and fsynced before the export action
is committed. Ordinary failures remove the newly created export and roll back
the journal entry. Crash-level atomicity across the database and output file is
not claimed. An interrupted export must be reviewed before reuse or publication.

## Uncertainty and comparison

Comparison begins with the sorted union of recorded file paths. For each path,
either a failed/unstable observation or a covering issue takes precedence over
an `added`, `removed`, or hash-equality conclusion. Directory issues cover an
entire subtree; root issues use `.`. This policy also applies to excluded symlink
paths, so replacing a known directory with a link does not appear to delete all
its descendants with certainty.

A subtree whose contents could not be enumerated may contribute no file rows.
Its error still appears in the report and makes the result incomplete. The report
cannot invent filenames for unobserved entries. Informational exclusions by
themselves do not make a snapshot incomplete within its defined supported scope.

Timeline records carry the corresponding file's read status. Metadata from an
unreadable file is a stat observation, not evidence that its content was acquired.
Reports do not rewrite recorded metadata or infer a real-world event from it.

## Representation and audit limits

Paths encode raw filesystem bytes per segment with percent escaping. This avoids
lossy Unicode conversions, embedded control characters, accidental separators,
and absolute paths in reports. The encoding is injective: a literal `%20` becomes
`%2520`. Database notes and the source binding are never copied to reports.

Audit entry hashes are SHA-256 over a canonical JSON array of sequence number,
UTC timestamp, action, canonical payload text, and previous hash. The genesis
predecessor is 64 zeroes. Verification checks sequence continuity, the creation
entry, and every linked digest. Payloads document action IDs/counts and export
digests, not a signed commitment to every row in the case database. Verification
does not reopen exports. The current head is printed for manual recording but
there is no external checkpoint verification workflow.

The journal is only a local change-detection aid. Rebuilding a chain, truncating a
valid suffix, restoring an earlier database, or changing unaudited snapshot data
can escape this check. A stronger claim would require a separate, trustworthy
anchor and a defined protocol that this version does not implement.
