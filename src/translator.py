import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import response
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

# Google GenAI SDK
from google import genai
from google.genai import types

# Workspace Paths
ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "manifests" / "program1.json"
DEFAULT_GROUP_CONFIG = ROOT / "config" / "measure_groups.json"
DEFAULT_SAS_OUTPUT_DIR = ROOT / "data" / "Project_1" / "SAS Output CSV"


# ---------------------------------------------------------------------------
# 1. Pydantic Output Schema for Gemini Structured Generation
# ---------------------------------------------------------------------------
class TranslationOutput(BaseModel):
    python_code: str = Field(
        description="Executable Python code snippet without markdown formatting."
    )
    notes: Optional[str] = Field(
        default="", description="Summary of SAS implicit behaviors handled."
    )

# ---------------------------------------------------------------------------
# 2. Gemini Translation Client
# ---------------------------------------------------------------------------
class GeminiSASTranslator:
    def __init__(self, api_key: Optional[str] = None):
        # Uses GEMINI_API_KEY environment variable if api_key is None
        self.client = genai.Client(api_key=api_key or os.environ.get("GEMINI_API_KEY"))
        self.model_name = "gemini-2.5-pro"

    def translate_macro(self, step_info: Dict[str, Any]) -> str:
        """Translates grp_score_macro into a standalone reusable Python function."""
        system_prompt = """
            You are an expert SAS-to-Python translator.
            Translate the SAS macro provided into a clean, vectorized Python function using pandas and numpy.

            CRITICAL SAS BEHAVIOR RULES TO IMPLEMENT:
            1. Preserve PROVIDER_ID as a string (handling leading zeros).
            2. Create presence flags C1..Cn (1 if measure is not NaN, else 0).
            3. total_cnt is sum of C1..Cn.
            4. measure_wt = 1 / total_cnt if total_cnt > 0, else NaN.
            5. score_before_std = sum of measures * measure_wt. Note: SAS sum(of ...) ignores missing values (skipna=True).
            6. Calculate sample Mean and sample StdDev (ddof=1) of score_before_std across non-null rows.
            7. grp_score = (score_before_std - Mean) / StdDev.
            8. Enforce exact output column order: [PROVIDER_ID, measures..., C1..Cn..., total_cnt, measure_wt, score_before_std, Mean, StdDev, grp_score].
            9. Return a function defined exactly as: def grp_score(df: pd.DataFrame, varlist: List[str]) -> pd.DataFrame:
            """

        user_prompt = f"""
            Translate the following SAS macro:
            SAS Code:
            ```sas
            {step_info.get('sas_code')}
            Implementation Notes from Manifest:
            {step_info.get('notes')}"""

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=TranslationOutput,
            ),
        )

        res: TranslationOutput = TranslationOutput.model_validate_json(response.text)
        return res.python_code

    def translate_group_step(self, step_info: Dict[str, Any], measures: List[str]) -> str:
        """Generates Python code to execute a measure group step using grp_score()."""
        system_prompt = """
            You are an automated code generator executing SAS group score steps in Python.
            You will be provided with:
                Input DataFrame in runtime named input_df.
                Measure list in runtime variable measures.
                Compiled grp_score(df, varlist) function available in runtime.
            Task: Generate Python code that invokes grp_score(input_df, measures) and assigns the resulting DataFrame to variable out_df.
        """
        user_prompt = f"""
            Step ID: {step_info.get('id')}
            Title: {step_info.get('title')}
            Target Measures: {measures}
            {step_info.get('sas_code')}
            Generate Python code assigning the output to out_df.
            """
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=TranslationOutput,
            ),
        )

        res: TranslationOutput = TranslationOutput.model_validate_json(response.text)
        return res.python_code

# ---------------------------------------------------------------------------
# 2. Gemini Translation Client
# ---------------------------------------------------------------------------
def run_translator():
    # Load manifests and configuration files
    with open(DEFAULT_MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(DEFAULT_GROUP_CONFIG, encoding="utf-8") as f:
        measure_groups = json.load(f)

    # Load base dataset
    input_csv = DEFAULT_SAS_OUTPUT_DIR / "STD_DATA_2025JUL_ANALYSIS.csv"
    input_df = pd.read_csv(input_csv, dtype={"PROVIDER_ID": str})
    input_df["PROVIDER_ID"] = input_df["PROVIDER_ID"].astype(str).str.zfill(6)

    translator = GeminiSASTranslator()

    # Dynamic execution namespace
    exec_scope = {"pd": pd, "np": np, "List": List}

    table_to_measures = {
        cfg["output_table"].upper(): cfg["measures"]
        for cfg in measure_groups.values()
    }

    all_passed = True

    print("=" * 80)
    print("GEMINI API POWERED SAS-TO-PYTHON TRANSLATOR & PARITY CHECK")
    print("=" * 80)

    for step in manifest:
        step_id = step["id"]
        kind = step["kind"]
        should_translate = step.get("translate", False)

        if not should_translate:
            print(f"[-] Step '{step_id}' ({kind}): SKIPPED")
            continue

        if kind == "macro":
            print(f"[+] Translating '{step_id}' via Gemini API...")
            macro_code = translator.translate_macro(step)

            # Compile generated macro function into execution scope
            exec(macro_code, exec_scope)

            if "grp_score" not in exec_scope:
                raise RuntimeError(
                    "Gemini translation failed to define 'grp_score' function."
                )

            print("    Successfully compiled 'grp_score' into execution context.")
            continue

        if kind == "group":
            output_table = step["outputs"][0].upper()
            measures = table_to_measures[output_table]

            print(
                f"[+] Translating step '{step_id}' (Output: {output_table}) via Gemini API..."
            )
            step_code = translator.translate_group_step(step, measures)

            # Prepare execution environment for step code
            exec_scope["input_df"] = input_df
            exec_scope["measures"] = measures

            # Run Gemini-generated step code
            exec(step_code, exec_scope)
            out_df = exec_scope.get("out_df")

            if out_df is None:
                print(f"Error: Gemini step code failed to produce 'out_df'.")
                all_passed = False
                continue
            
if __name__ == "__main__":
    run_translator()