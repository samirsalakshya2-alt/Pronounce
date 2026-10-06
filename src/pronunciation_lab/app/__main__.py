"""Start the Pronunciation Lab app.

    uv run python scripts/run_app.py            # opens http://127.0.0.1:8642/
    uv run python scripts/run_app.py --port 9000 --no-browser
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from pronunciation_lab.app.server import LabServer
from pronunciation_lab.app.service import AnalysisService
from pronunciation_lab.reader.service import ReaderService
from pronunciation_lab.reader.store import LocalFileStore

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pronunciation-lab")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: localhost only)")
    parser.add_argument("--port", type=int, default=8642)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data",
                        help="where the (optional) benchmark recordings and listening notes live")
    parser.add_argument("--notes-file", type=Path, default=None,
                        help="listening notes file (default: <data-dir>/listening_notes/notes.jsonl)")
    parser.add_argument("--reader-dir", type=Path, default=None,
                        help="where reading sessions and their recordings are kept "
                             "(default: ~/.pronunciation_lab/reader, outside the repository)")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-warmup", action="store_true", help="load the model on first analysis instead")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    notes = args.notes_file or args.data_dir / "listening_notes" / "notes.jsonl"
    service = AnalysisService(data_dir=args.data_dir, notes_path=notes)
    reader = ReaderService(LocalFileStore(args.reader_dir), service)
    try:
        server = LabServer((args.host, args.port), service, verbose=args.verbose, reader=reader)
    except OSError as exc:
        reader.close()
        service.close()
        print(f"Could not start on {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 2

    usable = [e["label"] for e in service.engines.values() if e["state"] == "runnable"]
    print(f"Pronunciation Lab running at {server.url}", flush=True)
    print(f"Reader: {server.url}read  (sessions in {reader.store.root})", flush=True)
    print(f"Engines available: {', '.join(usable) or 'none'}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    if not args.no_warmup:
        threading.Thread(target=service.warmup, name="warmup", daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(server.url + "read",)).start()

    def stop(signum, frame):  # noqa: ARG001
        threading.Thread(target=server.shutdown, daemon=True).start()

    # Handle SIGINT explicitly too: a process started in the background of a
    # non-interactive shell inherits SIGINT as "ignored", and Ctrl+C / kill -INT
    # would then never reach Python's KeyboardInterrupt.
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        reader.close()
        service.close()
        print("Stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
