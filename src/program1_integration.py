"""Connect Program 1 translation, execution, and SAS validation.

Command line: python -m src.program1_integration
This asks Gemini for the Program 1 code and saves it. It does not run the code.
"""

import argparse
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

from src.orchestrate import (
    DEFAULT_GROUP_CONFIG,
    DEFAULT_MANIFEST,
    DEFAULT_SAS_OUTPUT_DIR,
    build_program1_plan,
    run_program1,
)
from src.validate import compare_detailed
from src.pipeline_retry import retry_call


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CODE_DIR = ROOT / "output" / "program1_reviewed_code"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "data"


def run_program1_with_feedback(
    translator, review_code, *, max_attempts=3,
    run_dir=ROOT / "output" / "program1_feedback",
    input_csv=None, manifest_path=DEFAULT_MANIFEST,
    config_path=DEFAULT_GROUP_CONFIG, sas_output_dir=DEFAULT_SAS_OUTPUT_DIR,
):
    """Generate, review, execute and repair Program 1 within a bounded run.

    review_code receives a mapping of step IDs to candidate file paths on
    every attempt. Only an explicit True allows execution. The caller must
    arrange review and isolated execution; exec itself is not a sandbox.
    max_attempts includes the initial translation. Runtime errors stop repair.
    Operational failures return ERROR reports with completed validation history.
    Invalid configuration and an existing run directory still raise before work.
    Review is synchronous; a REVIEW_REQUIRED run cannot be resumed here.
    """
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer.")
    if not callable(review_code):
        raise TypeError("review_code must be callable.")
    plan = build_program1_plan(manifest_path, config_path)
    run_dir = Path(run_dir)
    # Never mix code or results from different runs.
    run_dir.mkdir(parents=True, exist_ok=False)
    history = []
    previous_code = None
    repair_steps = None
    feedback = None

    def report(status, code_dir, error=None):
        # Keep the latest completed validation details separate from a failed
        # candidate, which has not necessarily reached execution or validation.
        latest = history[-1] if history else {"steps": [], "outputs": {}}
        return {
            **latest,
            "status": status,
            "passed": status == "PASS",
            "attempts": list(history),
            "attempt": attempt,
            "code_dir": str(code_dir),
            "error": error,
        }

    for attempt in range(1, max_attempts + 1):
        attempt_dir = run_dir / f"attempt_{attempt}"
        code_dir = attempt_dir / "code"
        stage = "generation" if attempt == 1 else "repair"
        repair_requests = 0

        def request_repair():
            nonlocal repair_requests
            repair_requests += 1
            return translator.repair_program1(repair_steps, previous_code, feedback)

        try:
            if attempt == 1:
                files = generate_program1_code(
                    translator, code_dir, manifest_path, config_path,
                )
            else:
                repaired, _ = retry_call(
                    request_repair,
                    max_attempts=3, delay_seconds=2.0,
                )
                expected = {step["id"] for step in repair_steps}
                if (not isinstance(repaired, dict) or set(repaired) != expected
                        or any(not isinstance(code, str) or not code.strip()
                               for code in repaired.values())):
                    raise ValueError("Repair must return non-empty code for each requested step.")
                code_dir.mkdir(parents=True)
                files = {}
                for step_id, code in {**previous_code, **repaired}.items():
                    path = code_dir / f"{step_id}.py"
                    path.write_bytes(code.encode("utf-8"))
                    files[step_id] = path

            stage = "review"
            if review_code(dict(files)) is not True:
                return report("REVIEW_REQUIRED", code_dir)

            stage = "execution"
            result = run_reviewed_program1(
                code_dir=code_dir, input_csv=input_csv,
                output_dir=attempt_dir / "data", manifest_path=manifest_path,
                config_path=config_path, sas_output_dir=sas_output_dir,
                on_error="report",
            )
            invalid_references = {
                table for table, output in result["outputs"].items()
                if output.get("validation", {}).get("duplicate_sas_ids")
                or output.get("validation", {}).get("blank_sas_keys")
            }
            result = {**result, "non_repairable_outputs": sorted(invalid_references)}
            failed_ids = {
                output["step_id"] for table, output in result["outputs"].items()
                if output["status"] == "FAIL" and table not in invalid_references
            }
            history.append({**result, "attempt": attempt, "code_dir": str(code_dir)})
            if (result["passed"] or attempt == max_attempts or any(
                step["status"] in {"ERROR", "BLOCKED"} for step in result["steps"]
            ) or not failed_ids):
                return report(result["status"], code_dir)

            stage = "feedback"
            # A shared macro change can affect every group, so rerun all groups.
            repair_steps = [step for step in plan
                            if step["kind"] == "macro" or step["id"] in failed_ids]
            previous_code = {step["id"]: step["generated_code"]
                             for step in result["steps"] if "generated_code" in step}
            feedback = {table: output["validation"]
                        for table, output in result["outputs"].items()
                        if output["status"] == "FAIL" and table not in invalid_references}
        except Exception as error:
            details = {
                "stage": stage,
                "attempt": attempt,
                "type": type(error).__name__,
                "message": str(error),
                "attempts": repair_requests if stage == "repair" else getattr(error, "attempts", 1),
                "cause_type": type(getattr(error, "original_error", error)).__name__,
            }
            return report("ERROR", code_dir, details)


def generate_program1_code(
    translator,
    code_dir=DEFAULT_CODE_DIR,
    manifest_path=DEFAULT_MANIFEST,
    config_path=DEFAULT_GROUP_CONFIG,
    *,
    max_attempts=3,
    retry_delay_seconds=2.0,
):
    """Generate and save code without executing it."""
    plan = build_program1_plan(manifest_path, config_path)
    code_dir = Path(code_dir)

    if code_dir.exists() and any(code_dir.iterdir()):
        raise FileExistsError(
            f"Code directory is not empty: {code_dir}. "
            "Use a new directory to avoid mixing generated versions."
        )

    code_dir.mkdir(parents=True, exist_ok=True)
    generated_files = {}

    for step in plan:
        if step["action"] == "skip":
            continue

        if step["kind"] == "macro":
            operation = lambda: translator.translate_macro(step)
        elif step["kind"] == "group":
            measures = step["group_config"]["measures"]
            operation = lambda: translator.translate_group_step(step, measures)
        else:
            raise ValueError(f"Unsupported step kind: {step['kind']}")

        code, _attempts = retry_call(
            operation,
            max_attempts=max_attempts,
            delay_seconds=retry_delay_seconds,
        )

        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"Step {step['id']} returned empty Python code.")

        path = code_dir / f"{step['id']}.py"
        path.write_text(code, encoding="utf-8")
        generated_files[step["id"]] = path

    return generated_files


def run_reviewed_program1(
    code_dir=DEFAULT_CODE_DIR,
    input_csv=None,
    output_dir=DEFAULT_OUTPUT_DIR,
    manifest_path=DEFAULT_MANIFEST,
    config_path=DEFAULT_GROUP_CONFIG,
    sas_output_dir=DEFAULT_SAS_OUTPUT_DIR,
    on_error="raise",
):
    """Execute previously reviewed code through the existing Orchestrator."""
    code_dir = Path(code_dir)
    output_dir = Path(output_dir)
    sas_output_dir = Path(sas_output_dir)

    if input_csv is None:
        input_csv = sas_output_dir / "STD_DATA_2025JUL_ANALYSIS.csv"

    # Confirm every translated step has code before executing anything.
    plan = build_program1_plan(manifest_path, config_path)
    for step in plan:
        if step["action"] == "translate":
            path = code_dir / f"{step['id']}.py"
            if not path.is_file():
                raise FileNotFoundError(f"Missing reviewed code: {path}")

    input_df = pd.read_csv(input_csv, dtype={"PROVIDER_ID": "string"})
    input_df["PROVIDER_ID"] = input_df["PROVIDER_ID"].str.zfill(6)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Clear earlier group outputs so a failed run cannot reuse them.
    for step in plan:
        if step["kind"] == "group":
            table = step["group_config"]["output_table"]
            (output_dir / f"{table}.csv").unlink(missing_ok=True)

    def translate_step(step):
        return (code_dir / f"{step['id']}.py").read_text(
            encoding="utf-8"
        )

    def execute_step(step, generated_code, dependency_outputs):
        scope = {"pd": pd, "np": np, "List": List}

        if step["kind"] == "macro":
            exec(generated_code, scope)

            grp_score = scope.get("grp_score")
            if not callable(grp_score):
                raise RuntimeError(
                    "Translated macro did not define callable grp_score."
                )

            return grp_score

        if step["kind"] == "group":
            grp_score = dependency_outputs.get("grp_score_macro")
            if not callable(grp_score):
                raise RuntimeError(
                    f"Step {step['id']} is missing grp_score dependency."
                )

            scope["grp_score"] = grp_score
            scope["input_df"] = input_df.copy(deep=True)
            scope["measures"] = list(step["group_config"]["measures"])

            exec(generated_code, scope)
            out_df = scope.get("out_df")

            if not isinstance(out_df, pd.DataFrame):
                raise TypeError(
                    f"Step {step['id']} must produce a pandas DataFrame."
                )

            table = step["group_config"]["output_table"]
            out_df.to_csv(output_dir / f"{table}.csv", index=False)

            return out_df

        raise ValueError(f"Unsupported execution kind: {step['kind']}")

    return run_program1(
        translate_step=translate_step,
        execute_step=execute_step,
        validate_output=compare_detailed,
        manifest_path=manifest_path,
        config_path=config_path,
        sas_output_dir=sas_output_dir,
        on_error=on_error,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ask Gemini for the Program 1 code and save it.")
    parser.add_argument("--code-dir", default=DEFAULT_CODE_DIR, help="where to save the code")
    args = parser.parse_args(argv)

    # Imported here so the rest of this module doesn't need the Gemini client.
    from src.translator import GeminiSASTranslator

    files = generate_program1_code(GeminiSASTranslator(), args.code_dir)
    print(f"Saved {len(files)} files to {args.code_dir}")
    print("Read the code before you run it. It runs with full access to your computer.")


if __name__ == "__main__":
    main()
