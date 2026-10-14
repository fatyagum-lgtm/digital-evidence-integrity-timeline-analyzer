import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from deita import audit
from deita.cli import main
from deita.models import AuditError, DeitaError
from deita.service import create_comparison, create_snapshot
from deita.storage import initialize, open_case


def test_audit_verifies_and_detects_modification_deletion_and_empty_chain(workspace):
    case, source = workspace
    with open_case(case) as store:
        create_snapshot(store, source)
        create_snapshot(store, source)
        assert audit.verify(store.connection)["entries"] == 3
        store.connection.execute("UPDATE audit SET payload = '{}' WHERE sequence = 2")
        with pytest.raises(AuditError, match="entry 2"):
            audit.verify(store.connection)
        with pytest.raises(AuditError):
            create_snapshot(store, source)
        assert len(store.list_snapshots()) == 2
        store.connection.execute("DELETE FROM audit WHERE sequence = 2")
        with pytest.raises(AuditError):
            audit.verify(store.connection)
        store.connection.execute("DELETE FROM audit")
        with pytest.raises(AuditError, match="empty"):
            audit.verify(store.connection)


def test_snapshot_and_binding_roll_back_on_storage_failure(workspace, monkeypatch):
    case, source = workspace
    (source / "file").write_text("synthetic")

    def interrupted(*_args):
        raise KeyboardInterrupt

    with open_case(case) as store:
        monkeypatch.setattr(audit, "append", interrupted)
        with pytest.raises(KeyboardInterrupt):
            create_snapshot(store, source)
        assert store.list_snapshots() == []
        assert store.info["source_path"] is None
        assert audit.verify(store.connection)["entries"] == 1


def test_existing_case_is_never_replaced(workspace):
    case, _ = workspace
    before = (case / "case.sqlite3").read_bytes()
    with pytest.raises(DeitaError, match="already exists"):
        initialize(case, "OTHER")
    assert (case / "case.sqlite3").read_bytes() == before


def test_invalid_id_does_not_create_directory(tmp_path):
    case = tmp_path / "case"
    with pytest.raises(DeitaError):
        initialize(case, "../../secret")
    assert not case.exists()


def test_database_symlink_and_hardlink_are_rejected(workspace):
    case, source = workspace
    database = case / "case.sqlite3"
    original = source.parent / "original.sqlite3"
    database.rename(original)
    database.symlink_to(original)
    with pytest.raises(DeitaError), open_case(case):
        pytest.fail("Unsafe database opened")
    database.unlink()
    os.link(original, database)
    with pytest.raises(DeitaError), open_case(case):
        pytest.fail("Unsafe database opened")


def test_missing_and_wrong_order_ids(workspace):
    case, source = workspace
    with open_case(case) as store:
        create_snapshot(store, source)
        create_snapshot(store, source)
        for before, after in ((2, 1), (1, 1), (1, 999)):
            with pytest.raises(DeitaError):
                create_comparison(store, before, after)
        assert store.list_comparisons() == []


def test_cli_json_errors_exit_codes_and_lists(workspace, capsys, monkeypatch):
    case, source = workspace
    file = source / "file"
    file.write_text("old")
    case_args = ["--case", str(case)]
    assert main(["snapshot", "create", *case_args, "--source", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["snapshot_id"] == 1
    assert main(["snapshot", "create", *case_args, "--source", str(source)]) == 0
    capsys.readouterr()
    assert main(["compare", *case_args, "--before", "1", "--after", "2"]) == 0
    capsys.readouterr()
    file.write_text("new")
    assert main(["snapshot", "create", *case_args, "--source", str(source)]) == 0
    capsys.readouterr()
    assert main(["compare", *case_args, "--before", "2", "--after", "3"]) == 1
    assert json.loads(capsys.readouterr().out)["summary"]["modified"] == 1
    assert main(["snapshot", "list", *case_args]) == 0
    assert len(json.loads(capsys.readouterr().out)["snapshots"]) == 3
    assert main(["comparison", "list", *case_args]) == 0
    assert len(json.loads(capsys.readouterr().out)["comparisons"]) == 2
    assert main(["snapshot", "show", *case_args, "--id", "999"]) == 2
    assert "does not exist" in capsys.readouterr().err
    with open_case(case) as store:
        store.connection.execute("UPDATE audit SET entry_hash = 'wrong' WHERE sequence = 1")
    assert main(["audit", "verify", *case_args]) == 4
    assert "entry 1" in capsys.readouterr().err


def test_cli_partial_and_nonexistent_case(workspace, capsys, monkeypatch):
    from deita import scanner

    case, source = workspace
    (source / "file").write_text("synthetic")

    def fail(_fd, _size):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(scanner, "hash_descriptor", fail)
    args = ["--case", str(case)]
    assert main(["snapshot", "create", *args, "--source", str(source)]) == 3
    assert json.loads(capsys.readouterr().out)["complete"] is False
    assert main(["timeline", *args, "--snapshot", "1"]) == 3
    capsys.readouterr()
    assert (
        main(
            [
                "export",
                *args,
                "--kind",
                "snapshot",
                "--id",
                "1",
                "--format",
                "json",
                "--name",
                "partial.json",
            ]
        )
        == 3
    )
    capsys.readouterr()
    assert (case / "exports" / "partial.json").exists()
    missing = case.parent / "missing"
    assert main(["snapshot", "list", "--case", str(missing)]) == 2
    assert not missing.exists() and "ENOENT" in capsys.readouterr().err


def test_cli_real_entry_point_and_help():
    result = subprocess.run(
        [sys.executable, "-m", "deita", "--help"], text=True, capture_output=True, check=True
    )
    assert "snapshot" in result.stdout and "Exit codes" in result.stdout
    command = str(Path(sys.executable).with_name("deita"))
    result = subprocess.run([command, "--version"], text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "deita 1.0.0"


def test_readme_demo_commands_run_verbatim(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    readme = (repository / "README.md").read_text()
    demo = readme.split("<!-- demo:start -->\n```bash\n", 1)[1].split("```\n<!-- demo:end -->", 1)[
        0
    ]
    root = tmp_path.resolve()
    (root / "scripts").mkdir()
    shutil.copyfile(repository / "scripts" / "demo_data.py", root / "scripts" / "demo_data.py")
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    result = subprocess.run(
        ["bash", "-e", "-c", demo], cwd=root, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads((root / "demo-work" / "case" / "exports" / "comparison.json").read_text())
    assert report["summary"] == {
        "unchanged": 2,
        "modified": 1,
        "added": 1,
        "removed": 1,
        "unreadable": 0,
    }
    assert report["complete"] is True
