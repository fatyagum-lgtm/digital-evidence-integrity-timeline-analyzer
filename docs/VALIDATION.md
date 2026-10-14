# Validation performed for version 1.0.0

This is a record of executed local checks, not a coverage claim or certification.

## Environment

| Component | Observed version |
| --- | --- |
| OS / architecture | Linux / x86_64 |
| Python | 3.12.14 |
| SQLite | 3.53.1 |
| pytest | 9.1.1 |
| Ruff | 0.17.0 |
| build | 1.6.1 |
| Isolated build backend | setuptools 84.0.0 |

## Executed checks

The commands below were run using the project's virtual environment, except for
the wheel-install check, which used a separate empty virtual environment.
| Hosted GitHub Actions matrix | All six jobs passed: Ubuntu and macOS × Python 3.11, 3.12, and 3.13 (October 14, 2026) |

| Command or procedure | Observed result |
| --- | --- |
| `python3 -m venv .venv` and `python -m pip install -e '.[dev]'` | Editable installation completed |
| `python -m pytest -q` | **55 passed**, no skipped tests |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | All checked files already formatted |
| Exact README demonstration block, executed with `bash -e` | Completed with expected comparison exit code 1 handled explicitly |
| Synthetic comparison | 2 unchanged, 1 modified, 1 added, 1 removed, 0 unreadable |
| JSON/CSV exports | Five report files written; included in `examples/` |
| `deita audit verify --case demo-work/case` | Valid local chain, 9 entries |
| `python -m build` | Source distribution and wheel built successfully without warnings after license metadata was updated |
| `python -m pip install --no-index --no-deps <built-wheel>` | Installed into a separate environment with no runtime dependencies |
| Full README demo from a separate directory using only the installed wheel | Passed; import location confirmed inside that environment's site-packages |
| Inspection of generated sample JSON/CSV | No absolute workspace/source paths or private notes present |

The suite covers known SHA-256 values and multi-chunk files; repeated content;
content, size, addition, deletion and metadata-only changes; empty sources;
permission/I/O errors; subtree uncertainty; UTF-8, control-character, percent and
non-UTF-8 filenames; symlinks, broken links, loops, hard links and FIFOs; link
substitution races; growth/truncation and mid-read changes; case/source overlap;
case markers inside a source; depth limits; source binding; safe exports; UTC
nanosecond precision; timeline semantics; journal tampering; transaction rollback;
CLI IDs, exit codes and the README workflow.

Permission and I/O failures are injected deterministically in unit tests, so
they still exercise the failure paths when the test runner has elevated access.
Link, FIFO, hashing, filesystem mutation and serialization tests use real
temporary filesystem objects. No real evidence or private user files were used.

During implementation, the first test pass found that a subprocess test relied
on an activated PATH. It was corrected to use the entry point belonging to the
current Python environment. Ruff formatting issues and a deprecated license
metadata format were also corrected before the final checks.

## Not verified here

- Native Windows and WSL. Native Windows is explicitly unsupported; WSL itself has not been validated.
- Privileged adversarial mount changes, network filesystems, power-loss recovery, millions of files, filesystem-specific timestamp accuracy, or forensic admissibility.
- External audit anchoring and independent forensic validation. No test coverage percentage is claimed.

These limits are separate from the implemented and exercised CLI behavior.
