"""Print watcher tests (ticket-007): mocked OS printing, real folders."""

from pathlib import Path

from scripts.print_watcher import default_dirs, main, process_once


def _pdf(path: Path, name: str = "doc.pdf") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    target = path / name
    target.write_bytes(b"%PDF-1.4 fake")
    return target


class TestProcessOnce:
    def test_prints_and_archives(self, tmp_path: Path):
        queue, done = tmp_path / "q", tmp_path / "d"
        _pdf(queue)
        calls = []
        printed = process_once(queue, done, print_fn=lambda p: calls.append(p))
        assert printed == ["doc.pdf"]
        assert len(calls) == 1
        assert not (queue / "doc.pdf").exists()

    def test_failed_print_keeps_file(self, tmp_path: Path):
        queue, done = tmp_path / "q", tmp_path / "d"

        def _boom(_path):
            raise OSError("no printer")

        _pdf(queue)
        assert process_once(queue, done, print_fn=_boom) == []
        assert (queue / "doc.pdf").exists()

    def test_ignores_non_pdf(self, tmp_path: Path):
        queue, done = tmp_path / "q", tmp_path / "d"
        (queue).mkdir(parents=True, exist_ok=True)
        (queue / "note.txt").write_text("x")
        assert process_once(queue, done, print_fn=lambda p: None) == []

    def test_missing_dirs_created(self, tmp_path: Path):
        queue, done = tmp_path / "q", tmp_path / "d"
        assert process_once(queue, done, print_fn=lambda p: None) == []
        assert queue.is_dir() and done.is_dir()

    def test_each_file_once(self, tmp_path: Path):
        queue, done = tmp_path / "q", tmp_path / "d"
        _pdf(queue, "a.pdf")
        _pdf(queue, "b.pdf")
        calls = []
        assert process_once(queue, done, print_fn=lambda p: calls.append(p)) == ["a.pdf", "b.pdf"]
        assert process_once(queue, done, print_fn=lambda p: calls.append(p)) == []
        assert len(calls) == 2


class TestCli:
    def test_once_mode(self, tmp_path: Path, monkeypatch):
        queue, done = tmp_path / "q", tmp_path / "d"
        _pdf(queue)
        monkeypatch.setattr("scripts.print_watcher.print_file", lambda p: None)
        assert main(["--once", "--queue", str(queue), "--done", str(done)]) == 0
        assert not (queue / "doc.pdf").exists()

    def test_default_dirs(self):
        queue, done = default_dirs()
        assert queue.name == "print_queue" and done.name == "printed"
