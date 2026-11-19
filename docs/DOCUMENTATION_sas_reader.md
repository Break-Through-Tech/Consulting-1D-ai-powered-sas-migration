# SAS Reader documentation

Two parts:
1. **Program 1 manifest (hand-written)**: issue #12. Below.
2. **Automatic parser (Programs 0 and 2)**: issue #36. See "SAS Reader: automatic parser" further down. It generates `manifests/program0.json` and `manifests/program2.json`; `program1.json` stays hand-written.

## What this is
The team is moving a SAS pipeline that scores hospitals into Python. Program 1 takes ~46
quality measures per hospital, in 5 groups (Mortality, Safety, Readmission, Patient
Experience, Timely & Effective Care), averages the measures each hospital has in each group,
then standardizes that average into a group score.

The reader does not translate anything. It splits the SAS into small labelled steps so the
translator can convert them one at a time, and tests prove the split is accurate.

## Files
- `manifests/program1.json`: 7 steps, written by hand. Each has the exact SAS text, its source
  file and line numbers, inputs, outputs, and notes. One step is the reusable macro `%grp_score`;
  five call it once per group; one is context only.
- `manifests/program0.json` and `manifests/program2.json`: written by the parser (see below).
- `config/measure_groups.json`: which measure columns belong to each group, in order, and the
  output file each group produces.
- `tests/test_sas_reader.py`: checks that `program1.json` matches the real SAS files and CSVs.

## Who reads the manifests
- **Program 1:** `src/program1_integration.py` and `src/orchestrate.py` translate `grp_score_macro`
  once, then call it for the five group steps with the measure lists from `config/measure_groups.json`.
  Follow the SAS behaviours in the macro's notes: missing values in sums, n-1 standard deviation,
  merge on text `PROVIDER_ID`, exact output column order. See `docs/program1_integration.md`.
- **Programs 0 and 2:** `build_step_plan` in `src/orchestrate.py` reads the manifest,
  `generate_step_code` in `src/step_runner.py` asks Gemini for one file per step, and
  `run_steps_report` runs the reviewed files. See `docs/step_runner.md`.
- **Validation:** `src/validate.py` and `src/validation_report.py` compare the results with the five
  Program 1 and the five Program 0/2 SAS output CSVs in `data/Project_1/SAS Output CSV/`, using a
  numeric tolerance. See `docs/validation_report.md`.

## Check it
Run `python -m unittest discover tests -v`. To spot-check a step, open its `source_file` at
`line_start` to `line_end` and compare with `sas_code`. Nothing under `data/` and no `.sas` file
should change.

---

# SAS Reader: automatic parser (issue #36)

The hand-written `manifests/program1.json` is now backed by a parser that builds
the same kind of manifest automatically. Program 0 and Program 2 manifests are
generated with it (Task #4: "break Programs 0 and 2 into small steps").

## Flow
```
SAS file ──► src/sas_reader (parser, no AI) ──► manifests/programN.json ──► translator (Gemini) ──► pipeline ──► validator
```
For Programs 0 and 2 the middle is `build_step_plan` → `generate_step_code` → review → `run_steps_report`.
The parser only splits SAS into steps. It never calls an AI or runs Python, so the
same file always gives the same manifest.

## Run it
```bash
python -m src.sas_reader 0 --out manifests/program0.json     # Program 0 (34 steps)
python -m src.sas_reader 2 --out manifests/program2.json     # Program 2 (31 steps)
python -m src.sas_reader 1                                   # Program 1: print only (program1.json stays hand-written)
```
From Python (e.g. the demo):
```python
from src.sas_reader import parse_program
steps = parse_program("data/Project_1/SAS Programs/2 - Second Stage_Weighted Average and Categorize Star_2025Jul.sas",
                      globals_paths=["data/Project_1/SAS Programs/0 - Data and Measure Standardization_2025Jul.sas",
                                     "data/Project_1/SAS Programs/1 - First stage_Simple Average of Measure Scores_2025Jul.sas"])
for s in steps:
    print(s["id"], s["kind"], s["line_start"], s["line_end"], s["inputs"], "->", s["outputs"])
```
Earlier programs are passed as `globals_paths` because SAS runs them in one session:
Program 2 uses `&measure_OM_cnt` from Program 1 and `&RESULTS` from Program 0. The CLI does this for you.

## Test it
```bash
python -m pytest tests/test_sas_parser_syntax.py tests/test_sas_parser_program1.py \
                 tests/test_sas_parser_programs.py tests/test_measure_groups_sync.py -q
```
| Test file | Checks |
|---|---|
| `test_sas_parser_syntax.py` | One test per SAS construct found in our files (comments, quotes, DATA/PROC, `ods output`, `%let`, `INTO :`, `call symputx`, `%if/%do/%end`, `%macro`, macro calls) |
| `test_sas_parser_program1.py` | **Golden test:** parser output for Program 1 matches `program1.json` (ids, kinds, lines, SAS code, outputs, depends_on, macro-variable names). Expected differences are listed at the top of the file. |
| `test_sas_parser_programs.py` | Programs 0 and 2: no unsupported steps, answer-key tables produced, every input made earlier, no SAS line dropped, committed JSON up to date |
| `test_measure_groups_sync.py` | `config/measure_groups.json` equals the measure lists Program 0 builds (PR #31 follow-up) |

**Parser correctness vs translation correctness:** these tests prove the steps are
split correctly. Whether the Python is right is checked by `src/validate.py`
against the SAS `.sas7bdat` files (#38). A wrong manifest is a parser bug; a
validator mismatch is a translation bug.

## Output format
Same fields as `program1.json`, plus two:
| Field | Meaning |
|---|---|
| `kind` | `context` (setup, not translated) · `macro` (a `%macro` definition) · `group` (Program 1's SQL count + macro call) · **`step` (new: any other DATA step, PROC or macro call; all of Programs 0 and 2)** |
| `inputs` / `outputs` | Dataset names. **Bare name** = permanent table in the R. library = the SAS answer-key file (e.g. `STD_DATA_2025JUL_ANALYSIS`). **`WORK.` prefix** = temporary table inside the program (e.g. `WORK.PTEXP`, a measure list, is not the answer-key `PTEXP`). |
| `macro_vars` | Each `&variable` the step uses: its `%let` value, or `runtime (set by <step or file>)` |
| `depends_on` | Steps that must run first: the macro it calls, the step that made each input, and the step that set each runtime macro variable |
| `sas_type` | `data`, `proc sql`, `proc transpose`, `macro_call`, ... |
| `status` | `supported`, or `unsupported` (then `translate` is false). There are none today. |

## Scope and limits
- Only the SAS in our 4 files (Parker: a general SAS parser isn't required). Anything else is marked `unsupported`, never guessed.
- Macros are not expanded or run. Runtime macro values (`INTO :`, `call symputx`) are not computed by the parser: the manifest records which step sets each one (`runtime (set by <step>)`), and the step runner supplies the value when that step runs (`new_macro_vars`, see `docs/step_runner.md`).
- `%if/%do/%end` is handled only inside a DATA step (the one place it appears, Program 0 line 130).
