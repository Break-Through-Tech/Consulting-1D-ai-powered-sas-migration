"""Export chart-ready SAS migration measurements from saved evidence."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from time import perf_counter

from src import data_io, validation_report
from src.run_pipeline import run_pipeline
from src.translation_history import LocalJSONHistoryStore, write_json_atomic


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS = ROOT / "demo" / "results.json"
CONFIG_FIELDS = ("model_name", "prompt_version", "column_context", "repair_loop", "fastclus_helper")
PASS_STATUSES = {"pass", "ok"}
FAIL_STATUSES = {"fail", "failed", "error"}
FIXTURE_MODELS = {0: "gemini-3.5-flash-lite", 1: None, 2: "gemini-3.5-flash-lite"}


def _steps_summary(steps):
    measured = [row["first_try_success"] for row in steps if row["first_try_success"] is not None]
    return {
        "first_try_passed": sum(measured),
        "first_try_measured": len(measured),
        "first_try_success_rate": sum(measured) / len(measured) if measured else None,
    }


def _status(record):
    return (record.get("result", {}).get("status") or record.get("status") or "").lower()


def _precedes(first, second):
    if "sequence" in first and "sequence" in second:
        return first["sequence"] < second["sequence"]
    return bool(first.get("recorded_at") and second.get("recorded_at")
                and first["recorded_at"] < second["recorded_at"])


def _first_try(candidate, outcomes, invalid_candidates):
    if candidate is None or candidate["origin"] != "generated":
        return None
    if candidate["record_id"] in invalid_candidates:
        return False
    first_try = None
    first_outcome = None
    for outcome in outcomes:
        status = _status(outcome)
        kind = outcome.get("result", {}).get("kind", candidate.get("step", {}).get("kind"))
        macro_complete = status == "complete" and kind == "macro"
        if status in PASS_STATUSES or macro_complete:
            passed = True
        elif status in FAIL_STATUSES:
            passed = False
        else:
            continue

        if first_try is None:
            first_try, first_outcome = passed, outcome
        elif _precedes(first_outcome, outcome):
            break
        elif passed != first_try:
            # Random record IDs do not establish the order of tied outcomes.
            return None
    return first_try


def _history_steps(records, *, complete_repair_history, execution_statuses=None):
    candidates_by_step = {}
    outcomes_by_candidate = {}
    repairs_by_step = Counter()
    invalid_candidates = set()
    for record in records:
        kind = record["type"]
        if kind == "candidate":
            candidates_by_step.setdefault(record["step_id"], []).append(record)
        elif kind == "outcome" and record.get("candidate_id"):
            outcomes_by_candidate.setdefault(record["candidate_id"], []).append(record)
        elif kind == "repair_request":
            repairs_by_step.update(record["step_ids"])
        elif kind == "generation_error" and record.get("candidate_id"):
            invalid_candidates.add(record["candidate_id"])
        elif kind == "request_error" and record.get("step_id"):
            candidates_by_step.setdefault(record["step_id"], [])

    steps = []
    for step_id in sorted(candidates_by_step.keys() | repairs_by_step.keys()):
        candidates = candidates_by_step.get(step_id, [])
        roots = [candidate for candidate in candidates if not candidate.get("previous_candidate_id")]
        initial = roots[0] if len(roots) == 1 else None
        initial_outcomes = outcomes_by_candidate.get(initial["record_id"], []) if initial else []
        if execution_statuses is None:
            final = candidates[-1] if candidates else None
            final_outcomes = outcomes_by_candidate.get(final["record_id"], []) if final else []
            status = _status(final_outcomes[-1]) if final_outcomes else None
        else:
            status = execution_statuses.get(step_id)
        steps.append({
            "id": step_id,
            "status": status,
            "first_try_success": _first_try(initial, initial_outcomes, invalid_candidates),
            "fix_attempts": repairs_by_step[step_id] if complete_repair_history else None,
        })
    return steps


def _output_summary(outputs):
    return {
        name: {
            "program": entry.get("program", data_io.OUTPUTS[name.lower()]["program"]),
            "status": entry["status"],
            "largest_difference": entry.get("largest_difference"),
        }
        for name, entry in outputs.items()
    }


def _execution_result(program, records):
    """Only the latest identifiable execution can supply current validation."""
    starts = [
        record for record in records
        if record["type"] == "execution_started" and "execution_id" in record
    ]
    if not starts:
        if any(r["type"] == "run" and r["metadata"].get("attempt") for r in records):
            return {"status": "INCOMPLETE", "step_statuses": {}}
        return next((r for r in reversed(records) if r["type"] == "run_finished"), {})
    execution = starts[-1]
    if len(starts) > 1 and not _precedes(starts[-2], execution):
        return {"status": "INCOMPLETE", "step_statuses": {}}

    candidate_ids = execution["candidates"]
    outcomes = {
        record["result"]["id"]: record["result"]
        for record in records
        if record["type"] == "outcome"
        and record.get("execution_id") == execution["execution_id"]
        and candidate_ids.get(record["result"]["id"]) == record["candidate_id"]
    }
    statuses = {step_id: outcome["status"].lower() for step_id, outcome in outcomes.items()}
    if program != 1:
        finished = next((r for r in reversed(records) if r["type"] == "run_finished"
                         and r.get("execution_id") == execution["execution_id"]), None)
        if finished and candidate_ids.keys() <= outcomes.keys():
            return finished
        return {"status": "INCOMPLETE", "step_statuses": statuses}
    outputs = {}
    for step in execution["execution_plan"]:
        if step["id"] not in candidate_ids or step["kind"] != "group":
            continue
        result = outcomes.get(step["id"], {})
        validation = result.get("validation", {})
        outputs[step["group_config"]["output_table"]] = {
            "status": result.get("status", "NOT_CHECKED"),
            "largest_difference": (
                validation_report._largest_difference(validation)
                if "column_stats" in validation else None
            ),
        }

    if candidate_ids.keys() - outcomes.keys():
        status = "INCOMPLETE"
    elif all(result["status"] in {"PASS", "COMPLETE"} for result in outcomes.values()):
        status = "PASS"
    else:
        status = "FAIL"
    return {
        "status": status,
        "outputs": outputs,
        "step_statuses": statuses,
    }


def history_result(program, records, measurement=None, *, execution_records=None):
    """Summarize a history run without exporting code, feedback, or input rows."""
    # Sequence numbers are local to one store; linked attempts arrive in loop order.
    if execution_records is None and all("sequence" in r for r in records):
        records = sorted(records, key=lambda r: r["sequence"])
    header = next(record for record in records if record["type"] == "run")
    metadata = header["metadata"]
    if metadata.get("program", program) != program:
        raise ValueError(f"History run {header['run_id']} does not belong to Program {program}.")
    completed = _execution_result(program, records if execution_records is None else execution_records)
    steps = _history_steps(
        records,
        complete_repair_history=metadata.get("repair_history") is True,
        execution_statuses=completed.get("step_statuses"),
    )
    measurement = measurement or {}
    configuration = {
        key: measurement.get("configuration", {}).get(key, metadata.get(key))
        for key in CONFIG_FIELDS
    }
    return {
        "id": header["run_id"],
        "source": "translation_history",
        "programs": [program],
        "implementation": "gemini",
        "configuration": configuration,
        "status": completed.get("status"),
        "elapsed_seconds": measurement.get("elapsed_seconds", completed.get("elapsed_seconds")),
        "time_scope": measurement.get("time_scope", completed.get("time_scope")),
        "cost_usd": measurement.get("cost_usd"),
        "steps": steps,
        **_steps_summary(steps),
        "outputs": _output_summary(completed.get("outputs", {})),
    }


def _program1_history_result(folder, measurements, *, member_id=None):
    """Join sequential attempt stores, retaining the initial run as the chart ID."""
    from src.translation_history import read_program1_attempts

    attempts = read_program1_attempts(folder)
    if member_id is not None and not any(records[0]["run_id"] == member_id for records in attempts):
        raise ValueError(f"History {member_id} is not part of the Program 1 feedback loop in {folder}")
    combined = [record for attempt in attempts for record in attempt]
    records = attempts[-1]
    result = history_result(1, combined, measurements.get(combined[0]["run_id"]), execution_records=records)
    # A validated attempt can still be followed by an unfinished review or repair.
    terminal = records[-1]
    if terminal["type"] != "feedback_status":
        result["status"] = "INCOMPLETE"
    elif terminal["status"] in {"PASS", "FAIL"}:
        if result["status"] != terminal["status"]:
            raise ValueError(f"Program 1 terminal status disagrees with execution history in {folder}")
    else:
        result["status"] = terminal["status"]
    return result


def _stored_result(program, store, run_id, measurements):
    root = Path(store)
    records = LocalJSONHistoryStore(root).read_run(run_id)
    header = next(r for r in records if r["type"] == "run")
    if program == 1 and header["metadata"].get("attempt"):
        return _program1_history_result(root.parent.parent.parent, measurements, member_id=run_id)
    return history_result(program, records, measurements.get(run_id))


def measured_program1_result(folder, measurements):
    """Join the first attempt's generation history to this run's observed repairs."""
    sidecar = folder / "measurement.json"
    if not sidecar.exists():
        return _program1_history_result(folder, measurements)
    evidence = json.loads(sidecar.read_text(encoding="utf-8"))
    histories = evidence["histories"]
    if not histories or histories[0]["attempt"] != 1:
        raise ValueError(f"No initial Program 1 history in {folder}")

    initial_id = histories[0]["run_id"]
    measurement = {
        **measurements.get(initial_id, {}),
        "configuration": evidence["configuration"],
        "elapsed_seconds": evidence["elapsed_seconds"],
        "time_scope": "generation_execution_and_validation_excluding_review",
    }
    repairs = Counter(step for targets in evidence["repair_requests"].values() for step in targets)
    for entry in histories:
        root = folder / f"attempt_{entry['attempt']}" / "code" / ".translation_history"
        records = LocalJSONHistoryStore(root).read_run(entry["run_id"])
        execution_id = entry["execution_id"]
        records = [
            record for record in records
            if "execution_id" not in record or record["execution_id"] == execution_id
        ]
        if execution_id and not any(record["type"] == "execution_started" for record in records):
            raise ValueError(f"Missing recorded execution {execution_id} in {root}")
        attempt = history_result(1, records, measurement)
        if entry["status"] is not None and attempt["status"] != entry["status"]:
            raise ValueError(f"History does not match the recorded Program 1 outcome in {root}")
        if entry["attempt"] == 1:
            result = attempt
            for step in result["steps"]:
                step["fix_attempts"] = repairs[step["id"]]
        elif execution_id and entry["status"] in {"PASS", "FAIL"}:
            statuses = {step["id"]: step["status"] for step in attempt["steps"]}
            for step in result["steps"]:
                step["status"] = statuses.get(step["id"])
            result["outputs"] = attempt["outputs"]

    result["status"] = evidence["status"]
    return result


def _offline_result(run_id, implementation, report, steps, elapsed):
    return {
        "id": run_id,
        "source": "reviewed_fixture" if implementation == "gemini" else "handwritten_baseline",
        "programs": [0, 1, 2],
        "implementation": implementation,
        "configuration": dict.fromkeys(CONFIG_FIELDS),
        "status": report["status"],
        "elapsed_seconds": round(elapsed, 3),
        "time_scope": "local_execution_and_validation",
        "cost_usd": 0,
        "steps": steps,
        **_steps_summary(steps),
        "outputs": _output_summary(report["outputs"]),
    }


def offline_results():
    """Replay the checked-in AI chain and handwritten chain on the same source data."""
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        code_dirs = {
            program: ROOT / "tests" / "fixtures" / f"program{program}_generated"
            for program in (0, 1, 2)
        }
        # The Program 1 runner writes history beside the files it executes.
        code_dirs[1] = copytree(code_dirs[1], root / "program1")
        start = perf_counter()
        fixture = run_pipeline(code_dirs=code_dirs, run_dir=root / "results", reviewed=True)
        elapsed = perf_counter() - start

    steps = [
        {
            "program": program,
            "id": step["id"],
            "status": step["status"].lower(),
            "first_try_success": None,
            "fix_attempts": None,
        }
        for program in (0, 1, 2)
        for step in fixture[f"program{program}"]["steps"]
    ]
    reviewed = _offline_result("reviewed-gemini-chain", "gemini", fixture["report"], steps, elapsed)
    reviewed["model_name_by_program"] = FIXTURE_MODELS

    start = perf_counter()
    report = validation_report.build_report(validation_report.baseline_tables(), "handwritten baseline")
    baseline = _offline_result("handwritten-chain", "handwritten", report, [], perf_counter() - start)
    return [reviewed, baseline]


def build_results(runs):
    return {
        "schema_version": 1,
        "runs": runs,
        "historical_observations": [
            {
                "program": 0,
                "source": "docs/step_runner.md",
                "gemini_runs": 10,
                "runs_passing_all_outputs": 1,
                "outputs_per_run": 3,
            },
            {
                "program": 1,
                "source": "README.md",
                "gemini_runs": None,
                "runs_passing_all_outputs": 5,
                "outputs_per_run": 5,
            },
            {
                "program": 2,
                "source": "tests/fixtures/program2_generated/README.md",
                "manual_feedback_rounds": 11,
                "steps_ok_on_initial_run": 6,
                "total_steps": 31,
                "outputs_passing_on_final_run": 2,
            },
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program1-run", type=Path, action="append", default=[],
                        help="include a Program 1 feedback loop, with optional measurement.json (repeatable)")
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument(
        "--history", nargs=3, action="append", default=[], metavar=("PROGRAM", "STORE", "RUN_ID"),
        help="add a run read from a .translation_history directory (repeatable)",
    )
    parser.add_argument(
        "--measurements", type=Path,
        help="JSON object keyed by run ID with optional measured configuration, time and cost",
    )
    parser.add_argument(
        "--replay-reviewed-fixtures", action="store_true",
        help="execute the checked-in, reviewed Python and the handwritten baseline",
    )
    args = parser.parse_args(argv)
    if not (args.history or args.program1_run or args.replay_reviewed_fixtures):
        parser.error("supply --history, --program1-run or --replay-reviewed-fixtures")
    if any(program not in {"0", "1", "2"} for program, _, _ in args.history):
        parser.error("history program must be 0, 1 or 2")

    measurements = json.loads(args.measurements.read_text(encoding="utf-8")) if args.measurements else {}
    runs = [
        _stored_result(int(program), store, run_id, measurements)
        for program, store, run_id in args.history
    ]
    runs.extend(measured_program1_result(folder, measurements) for folder in args.program1_run)
    if len({run["id"] for run in runs}) != len(runs):
        parser.error("history run IDs must be unique")
    if args.replay_reviewed_fixtures:
        runs.extend(offline_results())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(args.output, build_results(runs))
    print(f"Wrote {len(runs)} runs to {args.output}")


if __name__ == "__main__":
    main()
