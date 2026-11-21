# Program 1 reviewed Gemini-generated code

These six files were copied from `output/program1_reviewed_code/` to
make the full pipeline reproducible for #32.

The original files are from the Program 1 Gemini-generation work in #17,
with local file timestamps from October 12, 2026. The project's generation
entry point is `python -m src.program1_integration`.

The saved code is executed through `run_reviewed_program1` without making
new Gemini API calls. The five group scripts use `grp_score`, which is
implemented in `grp_score_macro.py`.

The copied files have the same code as the originals. The only formatting
change was removing trailing whitespace from blank lines.

No files import the handwritten `src.baseline` implementation. The
integration test in `tests/test_full_chain_generated_fixture.py` verifies
the complete Program 0 -> 1 -> 2 chain against all 10 SAS reference outputs.
