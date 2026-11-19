"""Write the workflow documentation for each SAS program from its manifest (#67).

    python -m src.generate_docs            writes docs/workflow/program0.md, program1.md, program2.md
    python -m src.generate_docs --check    exits 1 if those files are out of date

No AI and no network: the structure comes from manifests/program*.json, so the same
manifests always give the same documents and every table row can be checked against the
SAS source (each step lists its file and lines). The short descriptions and the "SAS
behaviours" lists are written by hand below, from the Python baselines in src/baseline and
the tests that check them against the SAS outputs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from src.step_runner import macro_name

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = ROOT / "docs" / "workflow"
PROGRAMS = (0, 1, 2)

TITLES = {
    0: "Program 0: Data and Measure Standardization",
    1: "Program 1: First stage, simple average of measure scores",
    2: "Program 2: Second stage, weighted average and star rating",
}

# What each program does, from the docstrings in src/baseline/program*.py.
OVERVIEW = {
    0: [
        "Count the hospitals that report each measure and exclude measures reported by 100 or fewer hospitals.",
        "Drop hospitals that have none of the included measures (`%keep_hos`).",
        "Write the number, minimum, maximum, mean and standard deviation of each measure (PROC MEANS).",
        "Standardize each measure to mean 0 and standard deviation 1 (PROC STANDARD).",
        "Flip the measures where a lower score is better, so a higher value always means better.",
        "Rename the standardized measures to `std_<measure>` in the analysis file, and build the measure list of each of the 5 groups for Program 1.",
    ],
    1: [
        "For each of the 5 measure groups (Mortality, Safety, Readmission, Patient Experience, Process), average the measures each hospital has (`%grp_score`).",
        "Standardize that average to mean 0 and standard deviation 1 across hospitals to get the group score.",
        "One output table per group, with the measures, a flag and count for each, the average and the group score.",
    ],
    2: [
        "Merge the 5 group scores into one row per hospital.",
        "Weighted average of the group scores gives the summary score. When a group is missing, its weight is shared among the groups that remain.",
        "`%report`: a hospital gets a star only with at least 3 groups of 3 or more measures, one of them Mortality or Safety.",
        "Reporting hospitals are split into peer groups by their number of groups (3, 4 or 5).",
        "`%kmeans`: within each peer group, cluster the summary scores into 5 clusters (PROC FASTCLUS, run twice). The stars are the clusters ranked by their means.",
        "National averages of the summary score and of each group score.",
    ],
}

# SAS behaviours that decided whether Python matched SAS. Each is checked by a test
# against the SAS output (tests/test_baseline_program*.py and the Program 0 fixture).
BEHAVIOURS = {
    0: [
        "Standard deviations are sample standard deviations (divide by n-1), in PROC MEANS and PROC STANDARD.",
        "A missing score is never counted: the hospital count per measure and `Total_m_cnt` only count non-missing values.",
        "A SAS name list such as `HAI_1-HAI_6` means the six names `HAI_1` to `HAI_6`.",
        "SAS ignores upper and lower case in names (`OP_18b` and `OP_18B` are the same variable); pandas does not.",
        "`%keep_hos` drops the helper columns `c1` to `cn` but keeps `Total_m_cnt`.",
        "`PROVIDER_ID` is text and keeps its leading zeros.",
    ],
    1: [
        "`sum(of ...)` and `mean(of ...)` skip missing values; a comparison with a missing number is not true.",
        "Both standard deviations are sample standard deviations (n-1).",
        "`PROVIDER_ID` is read as text, so leading zeros are kept.",
        "Output columns are in a fixed order: `PROVIDER_ID`, the measures, `C1`..`Cn`, count, weight, score before standardizing, `Mean`, `StdDev`, `grp_score`.",
    ],
    2: [
        "PROC FASTCLUS has no exact Python equivalent. `src/sas_procs.py` has a hand-written `fastclus` that reproduces every SAS star; scikit-learn's `KMeans` gives a different star for 73 of 2,891 hospitals.",
        "The cluster seeds are the quintile medians, computed with SAS's default percentile definition (PCTLDEF=5), which differs from numpy's.",
        "The second FASTCLUS run uses `strict=1`, which has no `KMeans` equivalent.",
        "Clustering is done separately inside each peer group, so the same star can mean a different summary-score range in different peer groups.",
        "Hospitals that do not report keep a blank peer group and a missing star.",
        "The macro variables `measure_<group>` and `measure_<group>_cnt` come from Programs 0 and 1.",
    ],
}

PYTHON = {
    0: "Hand-written version: `src/baseline/program0.py`. AI version: `python -m src.generate_steps 0`, then `python -m src.run_program 0` (see `docs/step_runner.md`).",
    1: "Hand-written version: `src/baseline/program1.py`. AI version: `python -m src.program1_integration` (see `docs/program1_integration.md`).",
    2: "Hand-written version: `src/baseline/program2.py`. AI version: `python -m src.generate_steps 2`, then `python -m src.run_program 2` (see `docs/step_runner.md`).",
}


def load_manifest(program):
    return json.loads((ROOT / "manifests" / f"program{program}.json").read_text(encoding="utf-8"))


def _produced(steps):
    return {name.upper() for step in steps for name in step["outputs"]}


def outside_inputs(steps):
    """Tables a program reads that none of its own steps makes."""
    produced = _produced(steps)
    return sorted({
        name for step in steps for name in step["inputs"]
        if name.upper() not in produced and not name.startswith("<")
    })


def permanent_outputs(steps):
    """Tables written to the R. library: the SAS answer-key tables (no WORK. prefix)."""
    return sorted({
        name for step in steps for name in step["outputs"]
        if "." not in name and not name.startswith("<")
    })


def _macro_parameters(step):
    """Parameter names from the macro's own header, e.g. %macro kmeans(in, out) -> in, out."""
    match = re.search(r"%macro\s+\w+\s*\(([^)]*)\)", step["sas_code"], re.IGNORECASE)
    if not match:
        return []
    return [name.split("=")[0].strip() for name in match.group(1).split(",") if name.strip()]


def _code(names):
    return ", ".join(f"`{name}`" for name in names) if names else "none"


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _what(step):
    if step["kind"] == "context":
        return "Setup (`%LET`, `LIBNAME`, `%INCLUDE`): not translated"
    if step["kind"] == "macro":
        return f"Defines macro `%{macro_name(step)}`"
    kind = step.get("sas_type") or step["kind"]
    return f"{kind}: {step['title']}"


def _source(step):
    name = step["source_file"].split("/")[-1]
    lines = f"{step['line_start']}" if step["line_start"] == step["line_end"] else f"{step['line_start']}-{step['line_end']}"
    return f"{name} {lines}"


def render(program):
    steps = load_manifest(program)
    parts = [
        f"# {TITLES[program]}",
        "",
        f"<!-- Generated by `python -m src.generate_docs` from manifests/program{program}.json. Do not edit by hand. -->",
        "",
        "## What it does",
        "",
    ]
    parts += [f"{number}. {text}" for number, text in enumerate(OVERVIEW[program], 1)]

    outside = outside_inputs(steps)
    outputs = permanent_outputs(steps)
    parts += ["", "## Inputs and outputs", "", f"- **Reads from outside the program:** {_code(outside)}"]
    parts += [f"- **Writes (the SAS answer-key tables):** {_code(outputs)}"]
    earlier = [
        (other, sorted(set(outside) & set(permanent_outputs(load_manifest(other)))))
        for other in PROGRAMS if other < program
    ]
    for other, shared in earlier:
        if shared:
            parts.append(f"- **From Program {other}:** {_code(shared)}")
    later = [
        (other, sorted(set(outputs) & set(outside_inputs(load_manifest(other)))))
        for other in PROGRAMS if other > program
    ]
    for other, shared in later:
        if shared:
            parts.append(f"- **Used by Program {other}:** {_code(shared)}")
    parts.append("- Names with a `WORK.` prefix are temporary tables inside the program.")

    macros = [step for step in steps if step["kind"] == "macro"]
    if macros:
        parts += ["", "## Macros", "", "| Macro | Parameters | SAS source |", "|---|---|---|"]
        for step in macros:
            parts.append(f"| `%{macro_name(step)}` | {_code(_macro_parameters(step))} | {_cell(_source(step))} |")

    # One row per run-time macro variable: who sets it and which steps read it.
    setters, readers = {}, {}
    for step in steps:
        for name, value in step["macro_vars"].items():
            if not str(value).startswith("runtime"):
                continue
            if "set in this step" in value:
                setters.setdefault(name, step["id"])
            else:
                origin = value.replace("runtime (set by ", "").rstrip(")")
                setters.setdefault(name, origin)
                readers.setdefault(name, []).append(step["id"])
    if setters:
        parts += [
            "", "## Macro variables set while the program runs", "",
            "| Variable | Set by | Read by (in this program) |", "|---|---|---|",
        ]
        for name in sorted(setters, key=str.lower):
            read = readers.get(name, [])
            parts.append(f"| `&{name}` | {_cell(setters[name])} | {_cell(_code(read))} |")

    parts += ["", "## Steps", "", f"{len(steps)} steps, in the order the SAS runs them.", "",
              "| Step | What it does | Reads | Writes | SAS source |", "|---|---|---|---|---|"]
    for step in steps:
        reads = [name for name in step["inputs"] if not name.startswith("<")]
        writes = [name for name in step["outputs"] if not name.startswith("<")]
        parts.append(
            f"| `{step['id']}` | {_cell(_what(step))} | {_cell(_code(reads))} | {_cell(_code(writes))} | {_cell(_source(step))} |"
        )

    parts += ["", "## SAS behaviours that mattered for matching the results", ""]
    parts += [f"- {text}" for text in BEHAVIOURS[program]]
    parts += ["", "## Python", "", PYTHON[program], ""]
    return "\n".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="only check that the files are up to date")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="folder to write to")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    stale = []
    for program in PROGRAMS:
        path = out_dir / f"program{program}.md"
        text = render(program)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.name)
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"Wrote {path}")
    if stale:
        print(f"Out of date: {', '.join(stale)}. Run: python -m src.generate_docs")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
