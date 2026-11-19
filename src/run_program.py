"""Run saved, reviewed Program 0 code and check it against SAS (#75).

    python -m src.generate_steps 0 --min-interval 5     writes the code (needs a key)
    (read every file in the code folder: it runs with full access to this computer)
    python -m src.run_program 0 --code-dir output/program0_generated_code --reviewed

Prints what happened to each step, then the validation report for the outputs of that
program. Exit code 0 means every step ran and every output of the program matches SAS.
Program 2 needs Program 1's results and macro variables first (#72).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src import data_io, validation_report
from src.orchestrate import build_step_plan
from src.step_runner import run_steps_report

ROOT = Path(__file__).resolve().parent.parent
PROGRAM_OUTPUTS = {
    0: ("LESS100_MEASURE", "MEASURE_AVERAGE_STDDEV_2025JUL", "STD_DATA_2025JUL_ANALYSIS"),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("program", choices=["0", "2"], help="SAS program to run")
    parser.add_argument("--code-dir", help="folder of saved <step id>.py files")
    parser.add_argument(
        "--reviewed", action="store_true",
        help="say that you read every file in the code folder; without it nothing runs",
    )
    args = parser.parse_args(argv)

    if args.program == "2":
        parser.error("Program 2 needs the Program 1 outputs and macro variables first (#72)")
    program = int(args.program)

    code_dir = Path(args.code_dir) if args.code_dir else ROOT / "output" / f"program{program}_generated_code"
    if not args.reviewed:
        parser.error(f"read every file in {code_dir} first, then add --reviewed")
    if not code_dir.is_dir():
        parser.error(f"code folder not found: {code_dir}")

    plan = build_step_plan(ROOT / "manifests" / f"program{program}.json")
    result = run_steps_report(
        plan, code_dir, reviewed=True,
        initial_datasets={"HC.ALLDATA_2025JUL": data_io.load_input()},
    )

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
    return 0 if outputs_pass and steps_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
