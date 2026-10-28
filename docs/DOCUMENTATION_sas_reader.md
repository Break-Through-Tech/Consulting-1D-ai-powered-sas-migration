# SAS Reader documentation

Two parts:
1. **Program 1 manifest (hand-written)**: issue #12, related #13 (Translator). Below.
2. **Automatic parser (Programs 0 and 2)**: issue #36. See "SAS Reader: automatic parser" further down. It generates `manifests/program0.json` and `manifests/program2.json`; `program1.json` stays hand-written.

## What this is
The team is moving a SAS pipeline that scores hospitals into Python. Program 1 takes ~46
quality measures per hospital, in 5 groups (Mortality, Safety, Readmission, Patient
Experience, Timely & Effective Care), averages the measures each hospital has in each group,
then standardizes that average into a group score.

This PR does not translate anything. It splits the SAS into small labelled steps so a
Translator can convert them one at a time, and adds tests proving the split is accurate.

## What's in this PR
- `manifests/program1.json`: 7 steps. Each has the exact SAS text, its source file and line
  numbers, inputs, outputs, and notes. One step is the reusable macro `%grp_score`; five call
  it once per group; one is context only.
- `config/measure_groups.json`: which measure columns belong to each group, in order,
  and the output file each group produces.
- `tests/test_sas_reader.py`: checks that the JSON matches the real SAS files and CSVs.

Both JSON files were written by hand for this prototype. Automating the split can come later;
the format and the tests would not change.

## For the reviewer
1. Run `python -m unittest discover tests -v`. All 8 tests should pass.
2. Spot-check one step: open its `source_file` at `line_start` to `line_end` and compare with `sas_code`.
3. Check `macro_vars` and `notes` for one group step against its `%grp_score(...)` call.
4. Confirm that nothing under `data/` and no `.sas` file changed (`git diff --stat main`).

## For the Translator (issue #13)
Start with `manifests/program1.json`. Translate `grp_score_macro` once as a Python function,
then call it for the five group steps, taking each measure list from
`config/measure_groups.json`. Follow the SAS behaviors in the `grp_score_macro` notes: missing
values in sums, n-1 standard deviation, merge on text `PROVIDER_ID`, exact output column order.
Check your results against the five SAS output CSVs in `data/Project_1/SAS Output CSV/`,
using a numeric tolerance.

---

# SAS Reader: automatic parser (issue #36)

The hand-written `manifests/program1.json` is now backed by a parser that builds
the same kind of manifest automatically. Program 0 and Program 2 manifests are
generated with it (Task #4: "break Programs 0 and 2 into small steps").

## Flow
```
SAS file ──► src/sas_reader (parser, no AI) ──► manifests/programN.json ──► translator (Gemini) ──► pipeline ──► validator
```
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
- Macros are not expanded or run; runtime macro values (`INTO :`, `call symputx`) aren't computed.
- `%if/%do/%end` is handled only inside a DATA step (the one place it appears, Program 0 line 130).

## What teammates need to do
| Who | What |
|---|---|
| Amanda (#37) | Translate `manifests/program0.json`. `kind: "step"` is new: translate it like a group step (SAS code + inputs/outputs + macro_vars → Python), one step at a time in `depends_on` order. Program 1 is unchanged. |
| Lizzette (#32, #34) | To run Program 0/2 through `src/orchestrate.py`, add `"step"` to `ALLOWED_KINDS`. Program 1 runs as before. |
| Hao (#38) | Bare output names are the answer-key tables; ignore `WORK.` names. Program 0's are `LESS100_MEASURE`, `MEASURE_AVERAGE_STDDEV_2025JUL` and `STD_DATA_2025JUL_ANALYSIS`. |
| Pawanjit (#44) | Optional demo section: dropdown of the SAS files → `parse_program()` → table of steps → a step's SAS next to its saved Python. No live AI call needed. |
| Shane (#43) | `sas_type` and `kind` let you report results per step type (e.g. first-try success for `proc sql` vs `data`). |
