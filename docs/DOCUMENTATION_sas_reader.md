# SAS Reader: Program 1 PR documentation

Issue #12. Related: #13 (Translator).

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
