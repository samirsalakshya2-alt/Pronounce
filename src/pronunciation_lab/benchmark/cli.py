"""Benchmark command line.

    PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli preflight
    PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli run      --run-id m2
    PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli validate --run-id m2
    PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli analyze  --run-id m2
    PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli repeat   --run-id m2 --recordings R01 R03 R10 R18
    PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli status   --run-id m2

`run` resumes by default: complete ok/partial cells are skipped. `--retry-failed`
also reruns failed cells; `--force` reruns everything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pronunciation_lab.benchmark.cells import atomic_write_json
from pronunciation_lab.benchmark.dataset import preflight
from pronunciation_lab.benchmark.engines import ENGINES

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_RESULTS_DIR = DEFAULT_DATA_DIR / "benchmark_results" / "runs"


def _log(message: str) -> None:
    print(message, flush=True)


def _select(report, recording_ids: list[str] | None):
    if not recording_ids:
        return report.recordings
    known = {r.recording_id: r for r in report.recordings}
    missing = [r for r in recording_ids if r not in known]
    if missing:
        raise SystemExit(f"unknown recording(s): {missing}")
    return [known[r] for r in recording_ids]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pronunciation_lab.benchmark.cli")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight")
    for name in ("run", "validate", "analyze", "repeat", "status"):
        p = sub.add_parser(name)
        p.add_argument("--run-id", required=True)
        p.add_argument("--engines", nargs="+", default=list(ENGINES))
        p.add_argument("--recordings", nargs="+")
        if name == "run":
            p.add_argument("--retry-failed", action="store_true")
            p.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    report = preflight(args.data_dir)

    if args.command == "preflight":
        print(json.dumps({k: v for k, v in report.as_dict().items() if k != "audio"}, indent=2))
        return 0 if report.ok else 1

    unknown = [e for e in args.engines if e not in ENGINES]
    if unknown:
        print(f"unknown engine(s): {unknown}; known: {list(ENGINES)}", file=sys.stderr)
        return 2

    run_dir = args.results_dir / args.run_id
    summaries = run_dir / "summaries"

    if args.command == "run":
        if not report.ok:
            print(json.dumps(report.as_dict()["problems"], indent=2), file=sys.stderr)
            print("preflight failed; not running", file=sys.stderr)
            return 1
        from pronunciation_lab.benchmark.runner import BenchmarkRunner

        runner = BenchmarkRunner(
            run_dir, args.run_id, _select(report, args.recordings), args.engines,
            data_dir=args.data_dir, log=_log,
        )
        result = runner.run(report, retry_failed=args.retry_failed, force=args.force)
        atomic_write_json(summaries / "preflight.json", report.as_dict())
        counts: dict[str, int] = {}
        for status in result.statuses.values():
            counts[status] = counts.get(status, 0) + 1
        print(json.dumps({
            "executed": len(result.executed),
            "recorded_without_execution": len(result.recorded_without_execution),
            "skipped": len(result.skipped),
            "statuses_this_session": counts,
            "duration_s": round(result.duration_s, 2),
        }, indent=2))
        return 0

    if not run_dir.is_dir():
        print(f"no such run: {run_dir}", file=sys.stderr)
        return 2
    recordings = _select(report, args.recordings)

    if args.command in ("validate", "status"):
        from pronunciation_lab.benchmark.validate import validate_run

        validation = validate_run(run_dir, recordings, args.engines, list(ENGINES))
        if args.command == "validate":
            atomic_write_json(summaries / "validation.json", validation)
            print(json.dumps({k: validation[k] for k in ("ok", "nominal_cells", "counts")}, indent=2))
            for issue in validation["issues"][:50]:
                print(f"  {issue['code']}: {issue['cell_id'] or ''} {issue['message']}")
            return 0 if validation["ok"] else 1
        print(json.dumps(validation["counts"], indent=2))
        return 0

    if args.command == "analyze":
        from pronunciation_lab.benchmark.analysis import write_analysis

        written = write_analysis(run_dir, recordings, args.engines)
        for path in written:
            print(path)
        return 0

    if args.command == "repeat":
        from pronunciation_lab.benchmark.repeatability import check_repeatability

        repeat = check_repeatability(run_dir, recordings, args.engines, log=_log)
        atomic_write_json(summaries / "repeatability.json", repeat)
        print(json.dumps(repeat["summary"], indent=2))
        return 0 if repeat["summary"]["all_identical"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
