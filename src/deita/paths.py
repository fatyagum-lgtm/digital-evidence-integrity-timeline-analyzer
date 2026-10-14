"""Fail-closed POSIX path operations. Never resolve through a symbolic link."""

import errno
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote_from_bytes

from deita.models import DeitaError

CASE_MARKER = ".deita-case"


def require_safe_platform() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.listdir not in os.supports_fd
    ):
        raise DeitaError("Safe descriptor-relative traversal requires Linux/macOS (or WSL).")


def absolute_path(value: str | Path) -> Path:
    expanded = Path(value).expanduser()
    if ".." in expanded.parts:
        raise DeitaError("Parent '..' components are not accepted; use a direct path.")
    # abspath is lexical normalization, NOT symlink resolution.
    return Path(os.path.abspath(expanded))


def directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


@contextmanager
def open_directory(path: Path) -> Iterator[int]:
    require_safe_platform()
    if not path.is_absolute():
        raise DeitaError("Internal directory paths must be absolute.")
    descriptor = os.open("/", directory_flags())
    try:
        for part in path.parts[1:]:
            child = os.open(part, directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def assert_disjoint(case: Path, source: Path) -> None:
    if case == source or case in source.parents or source in case.parents:
        raise DeitaError(
            "Case and source directories must be disjoint, with neither inside the other."
        )


def relative_path(parts: tuple[str, ...]) -> str:
    for part in parts:
        if part in {"", ".", ".."} or "/" in part or "\x00" in part:
            raise DeitaError("Unsafe relative filename component.")
    return "/".join(quote_from_bytes(os.fsencode(part), safe="-._~") for part in parts)


def error_code(error: OSError) -> str:
    # Never persist str(error): it may contain absolute paths or private filenames.
    return errno.errorcode.get(error.errno or 0, "OS_ERROR")


def contains_case_marker(descriptor: int) -> bool:
    try:
        os.stat(CASE_MARKER, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True
