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

### Optional runtime repair

After reviewing the generated files, add `--repair` to the run command to enable
Gemini repair for that working directory. This applies to Programs 0 and 2.

```sh
python -m src.run_program 0 --code-dir output/program0_generated_code --reviewed --repair
```

Only failed steps receive a new request, with the error and previous code.
For a caller, the feedback also includes its directly required macro source and
the saved macro candidate ID.
Review each replacement before approving it. Approved replacements update the
working files and the plan runs again from its original inputs. Blocked steps
reuse their code after their dependencies recover. Missing input tables and
runner setup errors are recorded as blocked. Each step has at most two
fix requests after initial generation, including syntax fixes already in its
history. Transient request retries are counted separately. SAS mismatches
alone do not trigger repair.

For older reviewed files or fixtures with no history, repair starts a working
copy under `output/step_repairs/` and prints its path. First-generation success
is unknown for imported files. Fix counts start at import. Reuse that working
copy to retain its remaining attempt budget. Replays without
`--repair` never request new code. Execution uses the existing host runner
with the current process's permissions, including during repair.

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
- **Column names** ignore case when exactly one column matches (`SasFrame`): `df['measure_Name']` finds `measure_name`. This works for a single name, a list (`df[['A', 'B']]`), `sort_values(by=)`, `drop(columns=)`, `groupby(by)`, `dropna(subset=)`, `drop_duplicates(subset=)` and `merge(on=, left_on=, right_on=)`. In a merge, the right frame's column takes the left frame's spelling, as in SAS.
- **Not covered:** `'x' in df.columns` (so name lists go through `expand_name_list(text, df.columns)`, which returns the real spelling), `pd.merge(a, b, on=)` as a function, and the column picked after a groupby (`df.groupby('g')['V']`).

## Results of a run

`run_steps_report` returns, for every step, `{"id", "kind", "status", "error", "blocked_by"}`:

- `ok`: ran and returned what it promised.
- `failed`: the code raised, or did not return its outputs or macro variables. `error` has the reason.
- `blocked`: not run because a step it depends on did not finish, or a macro variable nobody set. `blocked_by` says which.
- `skipped`: a context step (setup, `%LET`, `%INCLUDE`), which is not translated.

It also returns `counts`, every `datasets` produced and every `macro_vars` resolved. `run_saved_steps` is the same flow, but stops at the first failure and raises.

## The translator

`GeminiSASTranslator` in `src/translator.py` has two sets of methods:

| Program | Methods | Used by |
|---|---|---|
| 1 | `translate_macro`, `translate_group_step`, `repair_program1` | `src/program1_integration.py` (`docs/program1_integration.md`) |
| 0 and 2 | `translate_macro_definition`, `translate_step` | `generate_step_code` in `src/step_runner.py` |

Both sets share `_generate_code`, which makes the request. The Program 1 methods were not changed for Programs 0 and 2.

**Where to put a new rule:** if Gemini keeps getting one thing wrong in a step, add one sentence to the right prompt constant in `src/translator.py`: `SAS_BEHAVIOUR_PROMPT` (how SAS behaves), `SAS_CONVENTIONS_PROMPT` (names and shapes), `SAS_PROCS_PROMPT` (the `sas_procs` helpers), `STEP_PROMPT` or `MACRO_DEFINITION_PROMPT`. Then generate again and check with `python -m src.run_program`.

## Prompts

The rules live in `src/translator.py`, in `STEP_PROMPT`, `MACRO_DEFINITION_PROMPT` and the shared `SAS_BEHAVIOUR_PROMPT`, `SAS_CONVENTIONS_PROMPT` and `SAS_PROCS_PROMPT`. When a step goes wrong, add the rule there. Do not edit `manifests/*.json` by hand: the parser writes them, and a test checks they are up to date.

Both translation methods accept `feedback=` for syntax and runtime failures.

## What was learned from real runs (#61, #82)

Ten generate, read, run passes on `gemini-3.5-flash-lite`:

- Most failures were ordinary, not wrong SAS logic: a column under another case, a name list expanded wrongly, a table with the wrong shape, a function that does not exist.
- **Gemini's output changes between runs.** Passes 7 to 9 each got 2 of 3 Program 0 outputs right, a different pair each time. Pass 10 got all 3. That run is saved as a test fixture (`tests/fixtures/program0_generated/`) and `tests/test_program0_generated_fixture.py` checks it with no Gemini call.
- **Program 2 took 11 rounds.** The first run ran 6 of 31 steps. Hao asked for the failed steps again by hand, with the error, the validation result and notes from reading the SAS code, until both outputs matched SAS. That run is saved as a test fixture (`tests/fixtures/program2_generated/`, `tests/test_program2_generated_fixture.py`, PR #95). About half of the rounds were column-case errors, which `SasFrame` now covers for lists, `sort_values`, `drop`, `groupby` and `merge` (#93).
- The runtime repair command automates re-asking failed steps. Assigning an output-only SAS mismatch to a particular step remains separate work.

## Not done yet

- **Program 2 from AI outputs.** `run_program 2` starts from the SAS outputs of Programs 0 and 1 and the measure lists and counts in `config/measure_groups.json` (`program2_inputs`, #72). Running it on the generated outputs of the earlier programs is part of the chain.
- **Program 0, 1 and 2 chained on generated code** (#32).
