"""Make or alter ONLY explicitly created synthetic demo files; not part of the scanner."""

import argparse
from pathlib import Path

MARKER = "DEITA synthetic demonstration data v1\n"
BASE = {
    "keep.txt": "A synthetic file that stays the same.\n",
    "notes.txt": "Synthetic interview draft.\n",
    "removed.txt": "This synthetic file disappears in phase two.\n",
    "nested/session.log": "2024-01-01T12:00:00Z synthetic login\n",
}


def create(root: Path) -> None:
    root.mkdir()  # Exclusive: never reuse a directory containing user data.
    (root / ".synthetic-demo").write_text(MARKER, encoding="utf-8")
    source = root / "source"
    (source / "nested").mkdir(parents=True)
    for name, content in BASE.items():
        (source / name).write_text(content, encoding="utf-8")


def change(root: Path) -> None:
    if root.is_symlink() or (root / ".synthetic-demo").read_text(encoding="utf-8") != MARKER:
        raise ValueError("Not an original synthetic demo directory.")
    source = root / "source"
    if source.is_symlink() or (source / "nested").is_symlink():
        raise ValueError("Synthetic demo directories must not be links.")
    for name, expected in BASE.items():
        file = source / name
        if (
            file.is_symlink()
            or file.stat().st_nlink != 1
            or file.read_text(encoding="utf-8") != expected
        ):
            raise ValueError("Demo input has already changed; use a new demo directory.")
    with (source / "added.txt").open("x", encoding="utf-8") as output:
        output.write("New synthetic evidence item.\n")
    (source / "notes.txt").write_text("Synthetic interview revised.\n", encoding="utf-8")
    (source / "removed.txt").unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("create", "change"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    {"create": create, "change": change}[args.phase](args.directory)
    print(f"Synthetic demo phase complete: {args.phase}")


if __name__ == "__main__":
    main()
