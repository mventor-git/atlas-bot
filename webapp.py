"""Atlas HQ Web App entry (ticket-037). Run on the HQ host, not from dev CLI.

    venv\\Scripts\\python webapp.py            # 127.0.0.1:8471
    venv\\Scripts\\python webapp.py --port 80  # host serving (needs rights)
"""
from __future__ import annotations

import argparse

from app.web import create_app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8471)
    args = ap.parse_args()
    create_app().run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
