# Program 1 Component Integration

## Purpose

Connect the existing Program 1 Orchestrator, Gemini SAS Translator,
Python execution, and SAS output Validator.

## Workflow

1. Configure a Gemini API key locally. Never commit API keys.
2. Call `generate_program1_code()` with a `GeminiSASTranslator`
   instance to generate the macro and five group files.
3. Review all generated Python files before execution.
4. Run approved code through `run_reviewed_program1()`.
5. Inspect the returned validation results and generated CSVs.

Generated code is saved to `output/program1_reviewed_code/` by
default. Despite the directory name, generation does not constitute
human review or approval.

## Relevant files

- `src/orchestrate.py` — execution plan and dependency ordering
- `src/translator.py` — Gemini-based SAS translation
- `src/program1_integration.py` — component integration
- `src/validate.py` — SAS output comparison
- `tests/test_program1_integration.py` — deterministic integration tests

## Safety

Generated Python is executed using `exec()`, which is not a sandbox.
Only execute reviewed code in a disposable, isolated environment
without accessible secrets or unrelated sensitive files.
Removing API keys from environment variables alone is insufficient.

## Real-data test results

Test input: `STD_DATA_2025JUL_ANALYSIS.csv`

All five generated outputs contained 4,566 rows, with matching
column order and provider IDs relative to the SAS reference outputs.

Gemini generated six Python files successfully, and the execution
and validation components ran end-to-end.

Strict SAS validation currently reports FAIL for all five outputs.
The remaining differences are small and concentrated in a few
numerical columns.

Across all five outputs, all Mean and StdDev values matched the
SAS references when compared at six decimal places. This supports
a reference-precision explanation but does not establish complete
numerical equivalence.

Final SAS parity remains under investigation as part of the
SAS comparison work. Validator tolerances have not been changed.

## Current limitations

- Execution of reviewed code is not sandboxed.
- Translation and execution exceptions are not yet converted into
  structured per-step failure results.
- Numerical parity with all five SAS outputs is not yet confirmed.
