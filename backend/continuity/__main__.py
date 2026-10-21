"""Cross-platform development and production entry point."""

import argparse
import asyncio
import os

import uvicorn


def loop_factory(use_subprocess: bool = False):
    # psycopg async connections need a selector loop on Windows as well as Unix.
    return asyncio.SelectorEventLoop()


def main():
    parser = argparse.ArgumentParser(description="Run Continuity")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    target = "continuity.api.app:app" if args.api_only else "continuity.server:app"
    uvicorn.run(target, host=args.host, port=args.port, reload=args.reload,
                loop="continuity.__main__:loop_factory", workers=1)


if __name__ == "__main__":
    main()
