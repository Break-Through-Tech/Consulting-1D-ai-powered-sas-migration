# Program 0 code written by Gemini (fixture)

These 33 files are the code `gemini-3.5-flash-lite` wrote for the Program 0 steps in pass 10 of issue #82 (Nov 19, 2026), saved by `python -m src.generate_steps 0 --min-interval 5`. Each file is one step of `manifests/program0.json`.

They were read before they were run, and they are not edited by hand. `tests/test_program0_generated_fixture.py` runs them through the step runner on the real input and checks the 3 outputs against SAS with `src/validation_report.py`, so the runner, the macro and name handling and the validator are tested without calling Gemini.

Gemini's output changes between runs. Passes 7, 8 and 9 each got 2 of 3 outputs right, and pass 10 got all 3. These files are one run that worked, not a promise that the next run will.

To replace them with a newer run: generate into a new folder, read every file, run them, check with `python -m src.validation_report`, and only then copy them here.
