"""Execute the staged step plan inside the Program 0/2 validation container."""
import json

import pandas as pd

from src.step_runner import run_steps_report


def read_input(path, dtypes):
    return pd.read_csv(path, dtype=dtypes, float_precision="round_trip",
                       keep_default_na=False, na_values=[""])


def run(root, code):
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    datasets = {name: read_input(root / f"input_{i}.csv", dtypes)
                for i, (name, dtypes) in enumerate(job["inputs"])}
    result = run_steps_report(job["plan"], code, reviewed=True, initial_datasets=datasets,
                              initial_macro_vars=job["macro_vars"])
    tables = {}
    for name, frame in result["datasets"].items():
        if name.upper() in job["outputs"]:
            if name.upper() in tables:
                raise ValueError("Ambiguous output table")
            tables[name.upper()] = frame.to_csv(index=False)
    return {"steps": result["steps"], "tables": tables}
