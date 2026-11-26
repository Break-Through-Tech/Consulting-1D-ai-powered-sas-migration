"""Check one saved Program 0 or 2 measurement offline without changing its evidence."""
import argparse
from collections import Counter
import json
from pathlib import Path

from src.migration_results import measured_step_result, repair_attempt_counts
from src.run_program import PROGRAM_OUTPUTS
from src.translation_history import LocalJSONHistoryStore
from src.validation_report import build_report


def verify(program, folder):
    summary = json.loads((folder / "measurement.json").read_bytes())
    ledger = folder / "requests.json"
    journal = json.loads(ledger.read_bytes()) if ledger.exists() else {}
    calls = journal.get("api_requests", [])
    print(summary["status"], "request slots", dict(Counter(c["status"] for c in calls)))
    limit = summary["request_limit"]
    if summary["program"] != program or not 1 <= limit <= 50 or len(calls) > limit:
        raise ValueError("Program or request budget differs from the measurement")
    if summary["status"] not in {"PASS", "FAIL"}:
        raise ValueError("No terminal validation to certify. Retain the partial history and request evidence.")
    if not calls or journal.get("request_limit") != limit:
        raise ValueError("Terminal measurement has a missing or unfinished request ledger")

    row = measured_step_result(folder)
    records = LocalJSONHistoryStore(folder / "code/.translation_history").read_run(row["id"])
    counts = repair_attempt_counts(records)
    if row["id"] != summary["run_id"] or any(s["fix_attempts"] != counts[s["id"]] for s in row["steps"]):
        raise ValueError("Exported run ID or repair counts differ from history")
    # Each returned candidate and each failed transport try has its own reserved slot.
    expected = Counter()
    for record in records:
        if record["type"] == "candidate" and record["origin"] in {"generated", "repair"}:
            expected[((record["step_id"],), record.get("repair_request_id"), "completed")] += 1
        elif record["type"] == "request_error":
            expected[((record["step_id"],), record.get("repair_request_id"), "failed")] += 1
    actual = Counter((tuple(c["step_ids"]), c["repair_request_id"], c["status"]) for c in calls)
    if actual != expected:
        raise ValueError("Request slots differ from saved candidate and retry history")

    end = next(r for r in reversed(records) if r["type"] == "run_finished")
    execution = folder / "executions" / end["execution_id"]
    saved = json.loads((execution / "validation.json").read_bytes())["outputs"]
    tables = {f.stem: f for f in execution.glob("*.csv")}
    checked = build_report(tables, "offline verification", [program])["outputs"]
    for name in PROGRAM_OUTPUTS[program]:
        if (checked[name] != saved[name] or saved[name] != end["outputs"][name]
                or checked[name]["status"] != row["outputs"][name]["status"]):
            raise ValueError(f"{name}: saved CSV, validation and history disagree")
    print(row["id"], "first try", row["first_try_passed"], "/", row["first_try_measured"],
          "repairs", dict(counts), "SAS", {k: v["status"] for k, v in row["outputs"].items()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", choices=(0, 2), type=int)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    try:
        verify(args.program, args.folder)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Evidence check stopped: {error}\n")


if __name__ == "__main__":
    main()
