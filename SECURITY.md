# Security policy and scope

DEITA is an educational local analysis tool. No version has been certified for
forensic acquisition, admissibility, or hostile-system examination. Version 1.x is
the current maintenance scope; there is no guaranteed response or patch timeline.

## Trust assumptions

Use authorized working copies in a stable local directory. Run as an ordinary
user and keep the case workspace private. The owner of the machine, the kernel,
filesystem implementations, mount topology, Python runtime, and case-directory
parents are trusted. This project does not isolate a compromised machine.

The scanner uses read-only opens, directory descriptors, `O_NOFOLLOW`, regular-file
checks, and pre/post fingerprints. Symbolic links are excluded without reading
their targets. Multiply linked files and cross-device traversal are excluded too.
These checks are defense in depth, not protection against every concurrent rename,
mount substitution, privileged mutation, or filesystem inconsistency.

The SQLite database and its sidecar names are managed by SQLite through paths.
Do not place the case in an untrusted shared directory or modify its tree during
operation. Reports never overwrite an existing file and cannot select arbitrary
output directories. As with any CLI, shell redirection is outside the program's
control; do not redirect stdout over an evidence file.

## Data handling

The application makes no network calls. Case databases contain absolute source
paths, private notes, relative filenames, metadata, and hashes. Reports remove
absolute workspace paths and notes but still expose potentially sensitive relative
names and hashes. Review exports before publishing; never include a real case
database in a public repository. Git ignores database files and the demo workspace.

The audit chain detects internally inconsistent journal entries, not authenticity.
It does not sign data, protect a database from its owner, verify snapshot tables,
or detect a correctly rehashed journal or valid suffix truncation. External trusted
checkpoints are not implemented.

## Reporting a problem

For the local project, provide the maintainer with a minimal reproduction using
synthetic files, the DEITA/Python/OS versions, the command, and the expected and
observed behavior. Do not send real evidence, a private database, credentials,
absolute personal paths, or sensitive report contents. No private security inbox
or hosted advisory channel is configured in this unpublished repository. Once a
repository is hosted, use its explicitly configured private reporting channel for
sensitive findings, if one is available; do not publish a sensitive exploit or
case data in a public issue.
