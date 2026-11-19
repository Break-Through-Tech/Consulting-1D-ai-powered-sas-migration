# Program 2 code written by Gemini (fixture)

These 31 files are the code `gemini-3.5-flash-lite` wrote for the Program 2 steps of `manifests/program2.json` (Nov 19, 2026, #38). Each file is one step.

They were read before they were run, and they are not edited by hand. `tests/test_program2_generated_fixture.py` runs them through the step runner from the SAS outputs of Programs 0 and 1 (`program2_inputs` in `src/run_program.py`) and checks `STAR_2025JUL` and `NATIONAL_AVERAGE_2025JUL` against SAS with `src/validation_report.py`. No Gemini call is made.

## How they were made

The first run (`python -m src.generate_steps 2 --min-interval 5`) ran 6 of 31 steps. The failed steps were then asked for again, one round at a time, with feedback, the way the repair loop for step manifests (#66, rule in `docs/validation_report.md`) is meant to work. It took 11 rounds of feedback before both outputs matched SAS (one round's answer was replaced before it was run, so there were 11 runs). Steps that ran in each run, of 31: 6 (the first run), 8, 20, 20, 20, 27, 13, 28, 31, 31, 31. The last run passes both outputs.

The feedback was of three kinds:

- **The step's error**, for example `KeyError: "['GRP_SCORE'] not in index"`, with the exact column names of the input tables.
- **The validation result**, for example 5 SAS columns missing in Python, or the columns in a different order.
- **Notes from reading the SAS code**, which a person added: `%report` reads the global measure lists `&measure_OM` ... `&measure_Process` (the manifest does not list them for that step, so Gemini was not told); `grp=cnt_grp` in `%kmeans` is a column name, not a macro variable; `PROC TRANSPOSE prefix=` adds no separator; `keep=` does not set the column order.

Twice a new answer undid an earlier fix (the `weight_` columns, and the key spelling in `nation_avg_peer`). The last rounds sent the previous working code and asked for one change, as the Program 1 repair loop does.

About half of the rounds came from case: SAS ignores it, pandas does not, and at the time the runner's case-insensitive lookup covered only `df['X']`. Lists of columns, `sort_values`, `drop`, `groupby` and `merge` were added afterwards (#93, `docs/step_runner.md`).

Gemini's output changes between runs. These files are one run that works, not a promise that the next run will.

To replace them with a newer run: generate into a new folder, read every file, run them with `python -m src.run_program 2 --code-dir DIR --reviewed`, and only then copy them here.
