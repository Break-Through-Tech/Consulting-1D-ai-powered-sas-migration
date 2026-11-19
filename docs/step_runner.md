# Programs 0 and 2: step translation and the step runner

How Gemini translates the `step` manifests of Programs 0 and 2, and how that code is run and checked. Program 1 has its own path (`docs/program1_integration.md`).

Where things are: `src/translator.py` (prompts and the two translation methods), `src/step_runner.py` (the runner), `src/generate_steps.py` and `src/run_program.py` (commands), `src/validation_report.py` (the check).

## The flow

```
python -m src.generate_steps 0 --min-interval 5     writes one <step id>.py per step. Needs GEMINI_API_KEY in .env
(read every file: it runs with full access to this computer)
python -m src.run_program 0 --code-dir output/program0_generated_code --reviewed
```

1. **Generate.** `generate_step_code` asks Gemini for each step in manifest order and saves `<step id>.py`. It checks that each file is valid Python (it only compiles it, nothing runs). If it is not, it asks again and quotes the error. Temporary API errors are retried.
2. **Review.** A person reads every file. `--reviewed` (or `reviewed=True`) says that you did. Nothing runs without it.
3. **Run.** `run_steps_report` runs the saved files in order and returns what happened to every step. It never calls Gemini, so a second run costs nothing.
4. **Check.** `src/run_program.py` passes the tables to `validation_report` and prints the result. Exit code 0 means every step ran and every output of that program matches SAS.

The free Gemini tier allows 15 requests a minute. `--min-interval 5` keeps a full run (about 33 requests) under that, and takes about 3 minutes.

## What generated code can use

Each step's code runs in a fresh scope with:

| Name | What it is |
|---|---|
| `datasets` | The step's input datasets, as copies, keyed by the names in the manifest (`'WORK.ALL_DATA_2025JUL'`). Key lookups ignore case. |
| `macro_vars` | SAS macro variables as strings (`macro_vars['measure_in']`). |
| `pd`, `np`, `List` | pandas, numpy and typing's `List`. |
| `sas_procs` | `fastclus` and `univariate_percentiles`, the hand-written versions of PROC FASTCLUS and PROC UNIVARIATE (#68). Nothing else. |
| `expand_name_list(text, columns)` | Splits a SAS name list, expands ranges (`HAI_1-HAI_6`) and spells each name as the dataset's column is spelled. |
| macro functions | One Python function for each macro already defined (`keep_hos`, `report`, `kmeans`, ...). |

A step must assign:

- `outputs`: a dict with every output dataset listed for the step, as DataFrames. Only the declared outputs leave the step.
- `new_macro_vars`: a dict, only for steps that set macro variables (CALL SYMPUT, PROC SQL INTO). Every variable the manifest says the step sets must be in it.

A macro definition step must define one function named after the macro. A macro takes the DataFrames, name lists (whitespace-separated strings) and counts that the SAS macro call passes.

## SAS ignores case, pandas does not

- **Dataset names** ignore case (`SasNames`). `WORK.ALL_DATA_2025Jul` finds `WORK.ALL_DATA_2025JUL`.
- **Column reads and writes** ignore case when exactly one column matches (`SasFrame`): `df['measure_Name']` finds `measure_name`. This does not cover `'x' in df.columns`, so name lists go through `expand_name_list(text, df.columns)`, which returns the real spelling.

## Results of a run

`run_steps_report` returns, for every step, `{"id", "kind", "status", "error", "blocked_by"}`:

- `ok`: ran and returned what it promised.
- `failed`: the code raised, or did not return its outputs or macro variables. `error` has the reason.
- `blocked`: not run because a step it depends on did not finish, or a macro variable nobody set. `blocked_by` says which.
- `skipped`: a context step (setup, `%LET`, `%INCLUDE`), which is not translated.

It also returns `counts`, every `datasets` produced and every `macro_vars` resolved. `run_saved_steps` is the same flow, but stops at the first failure and raises.

## Prompts

The rules live in `src/translator.py`, in `STEP_PROMPT`, `MACRO_DEFINITION_PROMPT` and the shared `SAS_BEHAVIOUR_PROMPT`, `SAS_CONVENTIONS_PROMPT` and `SAS_PROCS_PROMPT`. When a step goes wrong, add the rule there. Do not edit `manifests/*.json` by hand: the parser writes them, and a test checks they are up to date.

Both translation methods take `feedback=`, a sentence that is added to the request. The generator uses it to say why the last answer was rejected. A repair loop (#66) can use it for run-time errors.

## What was learned from real runs (#61, #82)

Ten generate, read, run passes on `gemini-3.5-flash-lite`:

- Most failures were ordinary, not wrong SAS logic: a column under another case, a name list expanded wrongly, a table with the wrong shape, a function that does not exist.
- **Gemini's output changes between runs.** Passes 7 to 9 each got 2 of 3 Program 0 outputs right, a different pair each time. Pass 10 got all 3. That run is saved as a test fixture (`tests/fixtures/program0_generated/`) and `tests/test_program0_generated_fixture.py` checks it with no Gemini call.
- Making it stable needs run-time errors and validator differences fed back to Gemini (#66).

## Not done yet

- **Program 2** on generated code (#33). `run_program` refuses it until Program 1's results and macro variables are handed over (#72).
- **Repair and history for steps** (#66).
- **Program 0, 1 and 2 chained on generated code** (#32).
