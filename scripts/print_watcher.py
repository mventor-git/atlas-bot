"""Print watcher: HQ print pipeline endpoint (ticket-007 / P0-2).

Polls exports/print_queue for approved PDFs, sends each to the OS
printer exactly once, then archives it under exports/printed/.

Run on the HQ box (it owns the printer):
    python scripts/print_watcher.py              # poll every 15s
    python scripts/print_watcher.py --once       # single pass (cron/task)
    python scripts/print_watcher.py --interval 30 --queue DIR --done DIR

Windows uses os.startfile(path, "print") - no extra dependency.
Other platforms: subclass or replace print_file().
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import date
from pathlib import Path


def default_dirs() -> tuple[Path, Path]:
    """Resolve queue/archive dirs relative to the repo root."""
    root = Path(__file__).parent.parent.resolve()
    return root / "exports" / "print_queue", root / "exports" / "printed"


def print_file(path: Path) -> None:
    """Send one PDF to the OS printer (Windows verb)."""
    if os.name != "nt":
        raise OSError(f"OS printing not implemented for {os.name}; override print_file().")
    os.startfile(str(path), "print")  # noqa: S606 - local file, operator action


def process_once(queue_dir: Path, done_dir: Path, print_fn=None) -> list[str]:
    """Print every queued PDF once and archive it. Returns printed names."""
    printer = print_fn or print_file
    queue_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)
    printed: list[str] = []
    for pdf in sorted(queue_dir.glob("*.pdf")):
        if not pdf.is_file():
            continue
        try:
            printer(pdf)
        except Exception:
            continue  # leave for next pass; never lose the file
        day_dir = done_dir / date.today().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(pdf), str(day_dir / pdf.name))
        except OSError:
            continue
        printed.append(pdf.name)
    return printed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HQ print-queue watcher.")
    parser.add_argument("--once", action="store_true", help="single pass then exit")
    parser.add_argument("--interval", type=int, default=15, help="poll seconds")
    parser.add_argument("--queue", type=str, default=None)
    parser.add_argument("--done", type=str, default=None)
    args = parser.parse_args(argv)

    queue_dir = Path(args.queue) if args.queue else default_dirs()[0]
    done_dir = Path(args.done) if args.done else default_dirs()[1]
    print(f"Watching {queue_dir} every {args.interval}s (Ctrl+C to stop).", flush=True)
    while True:
        for name in process_once(queue_dir, done_dir):
            print(f"Printed + archived: {name}", flush=True)
        if args.once:
            return 0
        try:
            time.sleep(max(1, args.interval))
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
