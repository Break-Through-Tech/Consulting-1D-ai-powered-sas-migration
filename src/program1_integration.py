"""Connect Program 1 translation, execution, and SAS validation."""

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


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CODE_DIR = ROOT / "output" / "program1_reviewed_code"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "data"


def generate_program1_code(
    translator,
    code_dir=DEFAULT_CODE_DIR,
    manifest_path=DEFAULT_MANIFEST,
    config_path=DEFAULT_GROUP_CONFIG,
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
            code = translator.translate_macro(step)
        elif step["kind"] == "group":
            measures = step["group_config"]["measures"]
            code = translator.translate_group_step(step, measures)
        else:
            raise ValueError(f"Unsupported step kind: {step['kind']}")

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
    )
