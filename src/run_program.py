"""Run saved, reviewed Program 0 or 2 code and check it against SAS (#75, #72).

    python -m src.generate_steps 0 --min-interval 5     writes the code (needs a key)
    (read every file in the code folder: it runs with full access to this computer)
    python -m src.run_program 0 --code-dir output/program0_generated_code --reviewed

Prints what happened to each step, then the validation report for the outputs of that
program. Exit code 0 means every step ran and every output of the program matches SAS.
Program 2 starts from the SAS outputs of Programs 0 and 1 (see program2_inputs).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src import data_io, validation_report
from src.orchestrate import DEFAULT_GROUP_CONFIG, build_step_plan, load_measure_groups
from src.step_runner import run_steps_report
from src.step_repair import run_steps_with_repair
from src.translation_history import CandidateHistory

ROOT = Path(__file__).resolve().parent.parent
PROGRAM_OUTPUTS = {
    0: ("LESS100_MEASURE", "MEASURE_AVERAGE_STDDEV_2025JUL", "STD_DATA_2025JUL_ANALYSIS"),
    2: ("STAR_2025JUL", "NATIONAL_AVERAGE_2025JUL"),
}


def program2_inputs(config_path=DEFAULT_GROUP_CONFIG):
    """The tables and macro variables Program 2 needs from Programs 0 and 1 (#72).

    Returns (datasets, macro_vars) for run_steps_report's initial_datasets and
    initial_macro_vars, so Program 2 is checked on its own:
      datasets    STD_DATA_2025JUL_ANALYSIS (Program 0) and the five group tables
                  (Program 1), as the SAS outputs. A SAS input always matches itself,
                  so the report only checks Program 2's own outputs.
      macro_vars  for each group in config/measure_groups.json, measure_<group> (its
                  std_ measure names, separated by one space, as Program 0's PROC SQL
                  INTO sets it) and measure_<group>_cnt (how many, as text, as
                  Program 1's PROC SQL count sets it).
    """
    groups = load_measure_groups(config_path)
    names = ["STD_DATA_2025JUL_ANALYSIS"] + [group["output_table"].upper() for group in groups.values()]
    datasets = {name: data_io.load_sas_output(name.lower()) for name in names}
    macro_vars = {}
    for key, group in groups.items():
        macro_vars[f"measure_{key}"] = " ".join(group["measures"])
        macro_vars[f"measure_{key}_cnt"] = str(len(group["measures"]))
    return datasets, macro_vars


def _repair_translator():
    from src.translator import GeminiSASTranslator
    return GeminiSASTranslator()


def _review_repair(step, code):
    print(f"Replacement for {step['id']}:\n{code}")
    return input("Run these exact replacement bytes? Type yes to approve: ").strip() == "yes"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("program", choices=["0", "2"], help="SAS program to run")
    parser.add_argument("--code-dir", help="folder of saved <step id>.py files")
    parser.add_argument("--repair", action="store_true",
                        help="ask Gemini for failed steps only and review each replacement")
    parser.add_argument(
        "--reviewed", action="store_true",
        help="say that you read every file in the code folder; without it nothing runs",
    )
    args = parser.parse_args(argv)

    program = int(args.program)

    code_dir = Path(args.code_dir) if args.code_dir else ROOT / "output" / f"program{program}_generated_code"
    if not args.reviewed:
        parser.error(f"read every file in {code_dir} first, then add --reviewed")
    if not code_dir.is_dir():
        parser.error(f"code folder not found: {code_dir}")

    plan = build_step_plan(ROOT / "manifests" / f"program{program}.json")
    if program == 0:
        datasets, macro_vars = {"HC.ALLDATA_2025JUL": data_io.load_input()}, {}
    else:
        datasets, macro_vars = program2_inputs()
    inputs = dict(reviewed=True, initial_datasets=datasets, initial_macro_vars=macro_vars)
    if args.repair:
        result = run_steps_with_repair(plan, code_dir, program=program, translator_factory=_repair_translator,
                                       review_repair=_review_repair, **inputs)
        code_dir = Path(result["code_dir"])
        print(f"Repair code and history: {code_dir}")
    else:
        result = run_steps_report(plan, code_dir, **inputs)

    print("STEPS")
    for entry in result["steps"]:
        line = f"  {entry['status'].upper():<8} {entry['id']}"
        if entry["error"]:
            line += f"   {entry['error']}"
        print(line)
    print("  " + ", ".join(f"{count} {status}" for status, count in result["counts"].items()))
    print()

    tables = {name: table for name, table in result["datasets"].items()
              if not name.upper().startswith(("WORK.", "HC."))}
    report = validation_report.build_report(tables, f"code in {code_dir}", programs=[program])
    print(validation_report.format_report(report))

    outputs_pass = all(report["outputs"][name]["status"] == "PASS" for name in PROGRAM_OUTPUTS[program])
    steps_ok = not (result["counts"]["failed"] or result["counts"]["blocked"])
    passed = outputs_pass and steps_ok
    if "execution_id" in result:
        CandidateHistory(code_dir).record({
            "type": "run_finished", "execution_id": result["execution_id"],
            "status": result.get("repair_status", "PASS" if passed else "FAIL"),
            "step_statuses": {s["id"]: s["status"] for s in result["steps"]},
            "outputs": {name: report["outputs"][name] for name in PROGRAM_OUTPUTS[program]},
        })
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
