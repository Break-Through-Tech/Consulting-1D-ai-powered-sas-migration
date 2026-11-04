"""Connect Program 1 translation, execution, and SAS validation."""

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
from src.translation_history import CandidateHistory, HistoryStorageError, error_details


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CODE_DIR = ROOT / "output" / "program1_reviewed_code"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "data"


def generate_program1_code(
    translator,
    code_dir=DEFAULT_CODE_DIR,
    manifest_path=DEFAULT_MANIFEST,
    config_path=DEFAULT_GROUP_CONFIG,
    *,
    max_attempts=3,
    retry_delay_seconds=2.0,
    history_store=None,
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
    metadata = {"manifest_path": str(manifest_path), "config_path": str(config_path)}
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
    *,
    history_store=None,
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

    input_df = pd.read_csv(input_csv, dtype={"PROVIDER_ID": "string"})
    input_df["PROVIDER_ID"] = input_df["PROVIDER_ID"].str.zfill(6)

    history = CandidateHistory(code_dir, history_store, metadata={
        "manifest_path": str(manifest_path), "config_path": str(config_path),
    })
    execution_id = uuid4().hex
    code_snapshot = {}
    candidates = {}
    for step in plan:
        if step["action"] == "translate":
            code = (code_dir / f"{step['id']}.py").read_bytes().decode("utf-8")
            code_snapshot[step["id"]] = code
            candidates[step["id"]] = history.candidate(
                step, code, origin="reviewed_file", reuse=True,
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
