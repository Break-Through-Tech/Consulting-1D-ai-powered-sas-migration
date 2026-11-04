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

The output CSVs we were first given were rounded, so correct code
failed against them, and all five outputs reported FAIL (#40).
Those files are now in `data/Project_1/archive/original_rounded_csv/`.
The CSVs in `SAS Output CSV/` hold the exact values from the
`.sas7bdat` files, and the validator's default is `rtol=1e-9`,
`atol=1e-12`.

With the exact CSVs, one Gemini run passed all five outputs through
this pipeline. Its largest difference from SAS was `1.07e-14`. This is
one run on Program 1 only, so each new run still needs validating.

If validation fails, look at the failing rows instead of raising the
tolerance.

## Current limitations

- Execution of reviewed code is not sandboxed.
- Code-generation failures can leave partially generated files;
  inspect the failure and use a new output directory when retrying.
- Configuration and pre-execution file errors still raise exceptions.
- Numerical parity with all five SAS outputs is not yet confirmed.

## Retry and Error Handling

Translation:
- Temporary API failures (429, 5xx) and network errors are retried.
- Maximum of 3 attempts, with exponential backoff starting at 2 seconds.
- Permanent errors are raised immediately without retrying.
- Exhausted transient retries raise `RetryFailure` with attempt information.

Validation:
- Temporary validation exceptions are retried up to 3 attempts.
- Numerical mismatches (`passed=False`) are reported without retrying.
- Validation retries do not regenerate or re-execute Python.

Pipeline reporting:
- `run_program1()` and `run_reviewed_program1()` support
  `on_error="report"` for structured step-level error results.
- The default `on_error="raise"` preserves existing behavior.
- Reports identify the failed step, stage, error type, and message.
- Retry exhaustion includes the attempt count and original error type.
- Steps requiring unavailable execution outputs are marked `BLOCKED`.
- Independent groups can continue after another group fails.
- Validation mismatches are reported but do not automatically block
  downstream execution.

Recovery:
- Inspect the reported failure before starting another run.
- Resolve permanent errors rather than repeating the same operation.
- Review all newly generated Python before executing it.
- Use a fresh code directory after interrupted generation to avoid
  mixing generated versions.
- Execute reviewed code only in an appropriately isolated environment.
