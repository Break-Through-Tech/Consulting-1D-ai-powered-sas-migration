# Validation report

One report that says how each of the 10 Python outputs compares with SAS. It comes in two forms with the same results: text for people and JSON for programs.

`src/validate.py` still does every comparison. `src/validation_report.py` only picks the right key for each output, gathers the results and writes them as sentences.

## Run it

Run these from the main project folder. Neither needs an API key, and neither runs generated code.

```
python -m src.validation_report --baseline      # the hand-written Python in src/baseline
python -m src.validation_report output/data     # a folder of Python outputs saved as CSV
```

- A folder must hold one file per output, named like `STAR_2025JUL.csv` and saved with `to_csv(index=False)`. Upper or lower case in file names does not matter, and other files are ignored.
- A missing value must be an empty cell, which is what `to_csv` writes. A word such as `NA`, `None` or `nan` is kept as text, so it does not match a missing value in SAS.
- Today the pipeline writes only Program 1's five tables into `output/data`. So the best result for that folder is `INCOMPLETE` (5 of 10 not checked), with exit code 1. That is expected. If one of the five differs, the result is `FAIL`.
- The report is printed, and saved as `validation_report.json` and `validation_report.txt` in `output/validation_report/`. Each run replaces the files there. Use `--out FOLDER` to save somewhere else. Git ignores `output/validation_report/` but not a new folder next to it, so keep other report folders inside it, for example `--out output/validation_report/run_2`.
- There is no option for the tolerance, on purpose. If an output fails, look at the rows.

The exit code says how the run ended.

| Exit code | Meaning |
|---|---|
| 0 | The report was written, and all 10 outputs match SAS. |
| 1 | The report was written, and something differs or was not checked. |
| 2 | The command could not finish, so do not read the report files: they can be left from an earlier run. Examples: the folder does not exist, the report cannot be saved, or the folder holds two files for one output (`STAR_2025JUL.csv` and `star_2025jul.csv`, which Linux allows). |

Python itself also ends with 1 when it cannot start the command at all, for example when it is run from another folder (`No module named 'src'`) or a package is not installed. Then there is an error message, no `RESULT:` line and no report. So a script should read the report files only when the last line printed starts with `RESULT:`.

## What is compared

`data_io.OUTPUTS` is where the report looks up how the rows of each output are matched with SAS.

| Output | Program | Rows matched by |
|---|---|---|
| `LESS100_MEASURE` | 0 | `MEASURE_NAME` |
| `MEASURE_AVERAGE_STDDEV_2025JUL` | 0 | `_STAT_` |
| `STD_DATA_2025JUL_ANALYSIS` | 0 | `PROVIDER_ID` |
| `OUTCOME_MORTALITY`, `OUTCOME_SAFETY`, `OUTCOME_READMISSION`, `PTEXP`, `PROCESS` | 1 | `PROVIDER_ID` |
| `STAR_2025JUL` | 2 | `PROVIDER_ID` |
| `NATIONAL_AVERAGE_2025JUL` | 2 | row position (it is one row with no identifying column) |

Every output is compared with its `.sas7bdat` file at `rtol=1e-9`, `atol=1e-12`. A match means:

- the same rows, the same columns in the same order, and every value equal within the tolerance;
- row order is **not** compared when rows are matched by a column;
- upper and lower case in column names is ignored, as in SAS.

`LESS100_MEASURE` has no rows in SAS, so a PASS there only shows that Python also made an empty table with the same two columns.

`run_program1` in `src/orchestrate.py` is not changed by this report. It still matches Program 1's five outputs by `PROVIDER_ID` itself, against the CSV copies in `SAS Output CSV`, which hold the same values.

## Results

Each output has one of four results.

| Result | Meaning |
|---|---|
| `PASS` | The Python table was compared and matches SAS. |
| `FAIL` | It was compared and something differs. |
| `ERROR` | A Python table was given but could not be read or compared, for example an unreadable file or something that is not a table. It is not known whether it matches SAS. |
| `NOT_CHECKED` | The output was not compared: no Python table was given for it, or its program was left out with `programs`. The report does not know why a table is absent; the run's own result does. |

The whole report has one of three.

| Result | Meaning |
|---|---|
| `PASS` | All 10 outputs are `PASS`. |
| `FAIL` | At least one output is `FAIL` or `ERROR`. |
| `INCOMPLETE` | Nothing failed, but at least one output is `NOT_CHECKED`. A partial run is never reported as `PASS`. |

## The readable report

The text starts with the result and a table of all 10 outputs. For each output that is not `PASS` it then says what differs. This is the part for one output, with some lines left out:

```
STAR_2025JUL (Program 2): FAIL
  Values differ in 12 columns:
  - STD_OUTCOMES_MORTALITY_SCORE: 884 rows differ (884 where SAS has no value and Python has one).
      First difference: PROVIDER_ID 010018: Python 0.0, SAS (missing)
  ...
  - SUMMARY_SCORE: 1,650 rows differ. Largest difference 6.43.
  - STAR: 75 rows differ. Largest difference 1.
  A first difference is shown for the first 5 columns only. The rest are in validation_report.json.
```

- Every column that differs gets a line with the number of rows. The first 5 columns also show their first row that differs, in the order of the Python table.
- A text value is shown in quotes. When SAS holds numbers and the Python column is not a number column (it is text, or numbers kept as Python objects), the line ends with "Compared as text." Text never equals a number, so such a column differs in every row that has a value.
- When only the shape differs (rows or columns are missing or extra), the text also says that every value in the rows and columns that both tables have is equal.
- Long lists of rows and of missing or extra columns are cut after 5 names. The JSON file keeps all of them.
- The text ends with a short guide to the words it uses.

## The JSON file

All 10 outputs are always listed, in pipeline order, under their SAS names in upper case. That is the spelling used by the manifests, by `run_program1()["outputs"]` and by the CSV file names.

```json
{
  "schema_version": 1,
  "created_at": "2026-11-12T01:05:00+00:00",
  "source": "CSV files in output/data (saved 2026-11-12 00:58 to 2026-11-12 01:04 UTC)",
  "programs": [0, 1, 2],
  "rtol": 1e-09,
  "atol": 1e-12,
  "status": "FAIL",
  "passed": false,
  "counts": {"PASS": 8, "FAIL": 1, "ERROR": 0, "NOT_CHECKED": 1},
  "outputs": {
    "OUTCOME_MORTALITY": {
      "program": 1,
      "key": "PROVIDER_ID",
      "status": "PASS",
      "largest_difference": 3.2e-14,
      "sas_text_columns": ["PROVIDER_ID"],
      "validation": {"passed": true, "python_rows": 4566, "sas_rows": 4566, "...": "..."}
    },
    "STAR_2025JUL": {
      "program": 2,
      "key": "PROVIDER_ID",
      "status": "FAIL",
      "largest_difference": 4.0,
      "sas_text_columns": ["PROVIDER_ID", "CNT_GRP"],
      "validation": {
        "passed": false,
        "mismatches": {
          "STAR": {
            "count": 613,
            "python_missing": 0,
            "sas_missing": 0,
            "examples": [{"PROVIDER_ID": "010021", "STAR_py": 5.0, "STAR_sas": 3.0}]
          }
        },
        "...": "..."
      }
    },
    "NATIONAL_AVERAGE_2025JUL": {"program": 2, "key": null, "status": "NOT_CHECKED"},
    "...": "the other seven outputs"
  }
}
```

| Field | Meaning |
|---|---|
| `schema_version` | 1. It is raised only when a field is renamed or removed. |
| `created_at` | When the comparison was made, in UTC. |
| `source` | Where the Python tables came from, in words. Show it; do not parse it. |
| `programs` | The programs this report covers. It is `[0, 1, 2]` unless `build_report` was given `programs`. An output of a program that is not listed is always `NOT_CHECKED`. |
| `rtol`, `atol` | The tolerance that was used. They are the validator's defaults and cannot be set here. |
| `status`, `passed` | The result of the whole report. `passed` is true only for `PASS`. |
| `counts` | How many outputs have each result. Always all four keys, adding up to 10. |
| `outputs[NAME].program` | The SAS program that writes the output: 0, 1 or 2. |
| `outputs[NAME].key` | The column rows are matched by, or `null` for row position. |
| `outputs[NAME].status` | `PASS`, `FAIL`, `ERROR` or `NOT_CHECKED`. |
| `outputs[NAME].validation` | Only for `PASS` and `FAIL`. Exactly what `compare_detailed()` returns, with the same field names. See `src/validate.py`. |
| `outputs[NAME].largest_difference` | Only with `validation`. The biggest gap between a Python number and the SAS number in the same cell, or `null` if no numbers were compared. |
| `outputs[NAME].sas_text_columns` | Only with `validation`. The SAS columns that hold text, in upper case. Every other SAS column holds numbers. |
| `outputs[NAME].error` | Only for `ERROR`: `{"type": ..., "message": ...}`, the same shape `run_program1` uses. |

Things to know when reading it:

- It is strict JSON. A missing value is `null`, and infinity is the text `"inf"` or `"-inf"`. (The history files write these differently, as tagged objects.)
- So the values of one column in `examples` can be numbers, `null` and the text `"inf"` together. `float(value)` turns `"inf"` and `"-inf"` back into numbers.
- An output without `validation` (`ERROR` or `NOT_CHECKED`) was not compared. Show a dash for its counts, not 0.
- Column names inside `validation` are in upper case, because the validator reports them that way.
- For an output matched by row position, example rows carry `"_ROW_POSITION_"`, counted from 0 and stored as text. The readable report says "row 1".
- `largest_difference` leaves out cells where either side is missing or infinite. It shows how close a `PASS` is. For a `FAIL`, read `mismatches`.
- New fields may be added without changing `schema_version`, so ignore fields you do not know. Renaming or removing a field raises it.
- Programs should read the JSON. The wording of the text report can change.

## Use it from Python

```python
from src.validation_report import (
    baseline_tables, build_report, compare_output, csv_tables, format_report, save_report,
)

# The hand-written Python.
report = build_report(baseline_tables(), "the hand-written Python in src/baseline")

# A folder of CSVs.
report = build_report(csv_tables("output/data"), "Gemini run, output/data")

# Tables in memory, for example the datasets from run_step_plan. WORK tables are ignored.
report = build_report(result["datasets"], "Gemini run of Program 2", programs=[2])

print(format_report(report))    # the readable text
save_report(report)             # writes both files
report["passed"]                # True only when all 10 match

# One table on its own, with the right key looked up for you.
validation = compare_output("STAR_2025JUL", star_table)
```

Pass `programs` when you report on one program that was given SAS tables as inputs. `run_step_plan` returns its inputs together with its results, and a SAS table always matches itself. With `programs=[2]`, only Program 2's two outputs are compared and the other eight are `NOT_CHECKED`.

Such a report is `INCOMPLETE` by design, because only all 10 outputs make a `PASS`. For the result of the one program, look at its own outputs:

```python
all(entry["status"] == "PASS" for entry in report["outputs"].values() if entry["program"] == 2)
```

`problem_lines(report["outputs"][NAME])` returns the plain sentences for one output, the same ones the text report prints.

## When a failure triggers a retry

This is the error threshold from #39 and #38. The tolerance is fixed, so there is no "small enough" difference: any `FAIL` is a real difference and is worth a repair. What decides a retry is whether a repair can fix it.

The Program 1 feedback loop (`run_program1_with_feedback` in `src/program1_integration.py`) already follows this rule:

| Result of an attempt | What happens |
|---|---|
| Every output passes | Stop: `PASS`. |
| An output fails | Retry: ask Gemini to repair the steps that wrote it (and every macro step), and send the `validation` of each failed output as feedback. |
| An output fails and its SAS table has repeated or blank keys (`duplicate_sas_ids`, `blank_sas_keys`) | Do not retry for it, whatever else differs. The problem is in the SAS reference, so new code cannot fix it. It is listed in `non_repairable_outputs`. If no other output failed, stop. |
| A step crashes or is blocked (`ERROR`, `BLOCKED`) | Stop. Runtime errors are not repaired. |
| The code is not approved in review | Stop: `REVIEW_REQUIRED`. |
| The attempt limit is reached | Stop with the last result. `max_attempts` is 3 by default and counts the first translation. |

This is not the same as the retries for temporary Gemini errors (`retry_call` in `src/pipeline_retry.py`), which repeat one request after a network or rate-limit error and never look at the results.

### Programs 0 and 2 (step manifests)

The loop for Programs 0 and 2 (#66) should repair run-time errors too. Hao and Miguel proposed this in #88 and #66; Shane (#66 owner) has not confirmed it yet. Most failures there are a step whose code raises (`failed` in `run_steps_report`), and asking for that step again with its error as `feedback=` is what makes runs stable. This rule is not built yet.

| Result of an attempt | What happens |
|---|---|
| Every output of the program passes | Stop: `PASS`. |
| A step is `failed` | Retry: ask again for that step, with its error as feedback. |
| A step is `blocked` | Do not ask for it. It runs again once the step it waits on is fixed. |
| An output fails | Retry, as in Program 1, with its `validation` as feedback. Which steps to ask for again is not decided yet (#71). A first version asks only for the `failed` steps. |
| An output fails and its SAS table has repeated or blank keys | Do not retry for it, as in Program 1. |
| The code is not approved in review | Stop. |
| The attempt limit is reached | Stop with the last result. Same limit as Program 1. |

Some run-time errors come from the runner, not the generated code, and asking Gemini again does not fix them reliably. Fix those in `src/step_runner.py` instead. For example, a column lookup that ignores case now covers `df['X']`, lists of columns, `sort_values(by=)`, `drop`, `groupby`, `dropna`, `drop_duplicates` and `merge` (#93). It does not cover `df.groupby('g')['V']` or `pd.merge(a, b, on=)`, so a failure there should be fixed in the runner too.

## Limits

- **The report checks tables, not how they were made.** It cannot tell a new CSV from one left by an older run. For a folder, `source` shows when the oldest and newest files were saved.
- **A number column stored as text fails in memory.** The validator compares a column of text or of Python objects as text, and text never equals a number. The same table saved as CSV passes, because a CSV file does not record the type. Fix the column in the Python code.
- **In a CSV, a word in place of a missing number makes the whole column text.** For example `to_csv(na_rep="NA")`. The column then fails as described above. Leave missing values empty.
- **A True/False column matches a SAS column of 1 and 0.** The validator reads True as 1 and False as 0, in memory and from a CSV. The report says `PASS`, although the saved CSV holds the words True and False where SAS holds 1 and 0.
- **A table with two column names that differ only by case** (`Mean` and `mean`) is an `ERROR`, because SAS would treat them as one column.
