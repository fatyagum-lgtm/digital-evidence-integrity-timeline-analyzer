from pathlib import Path

import pytest

from deita.storage import initialize


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    # macOS system temporary paths can contain /var -> /private/var aliases.
    root = tmp_path.resolve()
    source = root / "source"
    source.mkdir()
    case = root / "case"
    initialize(case, "TEST-001", notes="Private notes /Users/private-user/not-for-export")
    return case, source
