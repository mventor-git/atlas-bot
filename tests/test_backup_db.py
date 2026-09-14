"""Backup tool regression (gate G9): snapshot, integrity, prune, errors."""

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from scripts.backup_db import main, prune, snapshot


def make_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE users (chat_id TEXT PRIMARY KEY,"
                " monthly_salary REAL)")
    con.execute("INSERT INTO users VALUES ('u1', 12000.0)")
    con.commit()
    con.close()


def test_snapshot_copies_and_verifies(tmp_path: Path):
    src = tmp_path / "atlas_bot.db"
    make_db(src)
    out = snapshot(src, tmp_path / "backups")
    assert out.exists() and out.name.startswith("atlas-")
    con = sqlite3.connect(str(out))
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute(
            "SELECT monthly_salary FROM users WHERE chat_id='u1'"
        ).fetchone()[0] == 12000.0
    finally:
        con.close()


def test_snapshot_missing_source_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        snapshot(tmp_path / "nope.db", tmp_path / "backups")


def test_prune_keeps_newest(tmp_path: Path):
    for name in ("atlas-20260101-000000.db", "atlas-20260102-000000.db",
                 "atlas-20260103-000000.db"):
        (tmp_path / name).write_text("x")
    removed = prune(tmp_path, keep=2)
    assert removed == [tmp_path / "atlas-20260101-000000.db"]
    assert (tmp_path / "atlas-20260103-000000.db").exists()


def test_main_end_to_end(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    src = tmp_path / "atlas_bot.db"
    make_db(src)
    rc = main(["--db", str(src), "--dest", str(tmp_path / "bk"), "--keep", "1"])
    assert rc == 0
    assert list((tmp_path / "bk").glob("atlas-*.db"))


def test_main_postgres_exits_2(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    assert main(["--dest", str(tmp_path / "bk")]) == 2
    assert "pg_dump" in capsys.readouterr().out
