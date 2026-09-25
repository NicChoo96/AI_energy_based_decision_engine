"""``python -m webui`` — serve the console on http://127.0.0.1:8765."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m webui", description="Rule mesh console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true", help="restart on file changes")
    args = parser.parse_args()
    uvicorn.run("webui.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
