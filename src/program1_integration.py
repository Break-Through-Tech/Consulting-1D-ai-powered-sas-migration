"""Connect Program 1 translation, execution, and SAS validation.

Command line: python -m src.program1_integration
This asks Gemini for the Program 1 code and saves it. It does not run the code.
"""

import argparse
from pathlib import Path
from typing import List
from uuid import uuid4

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
from src.translation_history import CandidateHistory, HistoryStorageError, code_hash, error_details


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CODE_DIR = ROOT / "output" / "program1_reviewed_code"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "data"


def run_program1_with_feedback(
    translator, review_code, *, max_attempts=3,
    run_dir=ROOT / "output" / "program1_feedback",
    input_csv=None, manifest_path=DEFAULT_MANIFEST,
    config_path=DEFAULT_GROUP_CONFIG, sas_output_dir=DEFAULT_SAS_OUTPUT_DIR, validator=None,
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
    if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
        raise ValueError("max_attempts must be an integer between 1 and 3.")
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
    loop_id = None

    def report(status, code_dir, error=None):
        # Keep the latest completed validation details separate from a failed
        # candidate, which has not necessarily reached execution or validation.
        latest = history[-1] if history else {"steps": [], "outputs": {}}
        if attempt_history is not None:
            attempt_history.record({"type": "feedback_status", "attempt": attempt,
                                    "status": status, "error": error})
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
        attempt_history = None
        attempt_dir = run_dir / f"attempt_{attempt}"
        code_dir = attempt_dir / "code"
        stage = "generation" if attempt == 1 else "repair"
        repair_requests = 0

        def request_repair():
            nonlocal repair_requests
            repair_requests += 1
            try:
                return translator.repair_program1(repair_steps, previous_code, feedback)
            except Exception as error:
                try:
                    attempt_history.record({"type": "request_error", "repair_request_id": request_id,
                                            "request_number": repair_requests, "error": error_details(error)})
                except Exception as storage_error:
                    raise HistoryStorageError(str(storage_error)) from storage_error
                raise

        try:
            if attempt == 1:
                files = generate_program1_code(
                    translator, code_dir, manifest_path, config_path,
                    feedback_attempt=True,
                )
                attempt_history = CandidateHistory(code_dir)
                loop_id = attempt_history.run_id
            else:
                previous = CandidateHistory(run_dir / f"attempt_{attempt - 1}" / "code")
                previous_ids = {key: value["candidate_id"] for key, value in previous.pointer["candidates"].items()}
                code_dir.mkdir(parents=True)
                attempt_history = CandidateHistory(code_dir, metadata={
                    "program": 1, "loop_id": loop_id, "previous_run_id": previous.run_id,
                    "attempt": attempt, "repair_history": True,
                })
                request_id = attempt_history.record({
                    "type": "repair_request", "step_ids": [step["id"] for step in repair_steps],
                    "previous_candidates": previous_ids, "feedback": feedback,
                })
                repaired, _ = retry_call(
                    request_repair,
                    max_attempts=3, delay_seconds=2.0,
                )
                expected = {step["id"] for step in repair_steps}
                for step in repair_steps:
                    code = repaired.get(step["id"]) if isinstance(repaired, dict) else None
                    if isinstance(code, str):
                        attempt_history.candidate(step, code, origin="repair",
                                                  previous_candidate_id=previous_ids[step["id"]],
                                                  repair_request_id=request_id)
                if (not isinstance(repaired, dict) or set(repaired) != expected
                        or any(not isinstance(code, str) or not code.strip()
                               for code in repaired.values())):
                    error = ValueError("Repair must return non-empty code for each requested step.")
                    attempt_history.record({"type": "generation_error", "repair_request_id": request_id,
                                            "step_ids": sorted(expected), "error": error_details(error)})
                    raise error
                files = {}
                for step in plan:
                    step_id = step["id"]
                    if step_id not in previous_code:
                        continue
                    code = repaired.get(step_id, previous_code[step_id])
                    if step_id not in repaired:
                        attempt_history.candidate(step, code, origin="carried_forward",
                                                  previous_candidate_id=previous_ids[step_id])
                    path = code_dir / f"{step_id}.py"
                    path.write_bytes(code.encode("utf-8"))
                    files[step_id] = path

            stage = "review"
            approved_hashes = {step_id: code_hash(path.read_bytes().decode("utf-8"))
                               for step_id, path in files.items()}
            if review_code(dict(files)) is not True:
                return report("REVIEW_REQUIRED", code_dir)

            stage = "execution"
            result = run_reviewed_program1(
                approved_hashes=approved_hashes,
                code_dir=code_dir, input_csv=input_csv,
                output_dir=attempt_dir / "data", manifest_path=manifest_path,
                config_path=config_path, sas_output_dir=sas_output_dir,
                on_error="report", validator=validator,
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
    history_store=None,
    feedback_attempt=False,
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
    metadata = {"program": 1, "manifest_path": str(manifest_path), "config_path": str(config_path)}
    if feedback_attempt:
        metadata.update(attempt=1, repair_history=True)
    model = getattr(translator, "model_name", None)
    if isinstance(model, str):
        metadata["model_name"] = model
    history = CandidateHistory(code_dir, history_store, metadata=metadata, new=True)
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

        requests = 0

        def request_code():
            nonlocal requests
            requests += 1
            try:
                return operation()
            except Exception as error:
                try:
                    history.record({
                        "type": "request_error", "step_id": step["id"],
                        "request_number": requests, "error": error_details(error),
                    })
                except Exception as storage_error:
                    # A backend timeout must not trigger another Gemini request.
                    raise HistoryStorageError(str(storage_error)) from storage_error
                raise

        code, _attempts = retry_call(
            request_code,
            max_attempts=max_attempts,
            delay_seconds=retry_delay_seconds,
        )

        candidate_id = None
        if isinstance(code, str):
            candidate_id = history.candidate(step, code, origin="generated")
        if not isinstance(code, str) or not code.strip():
            history.record({
                "type": "generation_error", "step_id": step["id"],
                "candidate_id": candidate_id,
                "error": {"type": "ValueError", "message": "Empty or non-string Python code."},
            })
            raise ValueError(f"Step {step['id']} returned empty Python code.")

        path = code_dir / f"{step['id']}.py"
        try:
            path.write_bytes(code.encode("utf-8"))
        except Exception as error:
            history.record({"type": "artifact_error", "candidate_id": candidate_id,
                            "step_id": step["id"], "error": error_details(error)})
            raise
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
    approved_hashes=None,
    *,
    history_store=None, validator=None,
):
    """Execute previously reviewed code through the existing Orchestrator."""
    if on_error not in ("raise", "report"):
        raise ValueError("on_error must be raise or report.")
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

    if validator is None:
        input_df = pd.read_csv(input_csv, dtype={"PROVIDER_ID": "string"})
        input_df["PROVIDER_ID"] = input_df["PROVIDER_ID"].str.zfill(6)

    history = CandidateHistory(code_dir, history_store, metadata={
        "manifest_path": str(manifest_path), "config_path": str(config_path),
    })
    execution_id = uuid4().hex
    code_snapshot = {
        step["id"]: (code_dir / f"{step['id']}.py").read_bytes().decode("utf-8")
        for step in plan if step["action"] == "translate"
    }
    if approved_hashes is not None and approved_hashes != {
        key: code_hash(code) for key, code in code_snapshot.items()
    }:
        raise ValueError("Program 1 files changed after review began. Review the new candidates before execution.")
    candidates = {}
    for step in plan:
        if step["action"] == "translate":
            candidates[step["id"]] = history.candidate(
                step, code_snapshot[step["id"]], origin="reviewed_file", reuse=True,
            )
    history.record({
        "type": "execution_started", "execution_id": execution_id,
        "review_status": "caller_supplied_reviewed_code", "candidates": candidates,
        "execution_plan": plan,
        "metadata": {"input_csv": str(input_csv), "sas_output_dir": str(sas_output_dir),
                     "manifest_path": str(manifest_path), "config_path": str(config_path)},
    })

    setup_operation = "create_output_directory"
    setup_path = output_dir
    try:
        output_dir.mkdir(parents=True, exist_ok=True)

        # Clear earlier group outputs so a failed run cannot reuse them.
        setup_operation = "remove_stale_output"
        for step in plan:
            if step["kind"] == "group":
                table = step["group_config"]["output_table"]
                setup_path = output_dir / f"{table}.csv"
                setup_path.unlink(missing_ok=True)
    except Exception as error:
        history.record({
            "type": "execution_error", "execution_id": execution_id,
            "stage": "setup", "operation": setup_operation,
            "path": str(setup_path), "error": error_details(error),
        })
        raise

    def translate_step(step):
        return code_snapshot[step["id"]]

    def record_step(result):
        if result["id"] in candidates:
            history.record({
                "type": "outcome", "candidate_id": candidates[result["id"]],
                "execution_id": execution_id,
                "validation_status": (
                    "not_applicable" if result["kind"] == "macro" else
                    "completed" if "validation" in result else "not_completed"
                ),
                "result": result,
            })

    if validator is not None:
        result = validator(code_snapshot, plan, input_csv, manifest_path, config_path,
                           sas_output_dir, output_dir)
        for step_result in result["steps"]:
            record_step(step_result)
        return result

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
        on_step_result=record_step,
        execution_plan=plan,
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
