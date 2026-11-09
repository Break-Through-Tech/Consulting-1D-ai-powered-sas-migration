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

Generated Python runs through `exec()`, which is not a sandbox. In the
feedback loop, it runs in the same process as the Gemini client holding the API
key.

Review every candidate, including repairs, and reject code that hard-codes SAS
reference values. Run the whole process in a disposable, isolated
environment without other secrets or sensitive files; removing an environment
variable does not remove a key already held by the client.

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
- The lower-level integration functions still raise configuration and
  pre-execution file errors. The feedback wrapper reports operational errors
  as described below; invalid configuration and run-directory setup errors
  still raise before a run starts.

## Retry and Error Handling

### Validation feedback repair (issue #41)

`run_program1_with_feedback(translator, review_code, max_attempts=3,
run_dir=...)` generates, reviews, executes, and validates Program 1. It asks
Gemini to repair the code when validation fails, up to the configured attempt
limit.

#### Run and review

Use a new `run_dir` for each run. The callback receives step IDs mapped to the
six candidate file paths on every attempt, including repairs. Inspect each file
before returning exactly `True` to approve execution:

```python
from src.program1_integration import run_program1_with_feedback
from src.translator import GeminiSASTranslator

def review(files):
    for step_id, path in files.items():
        print(step_id, path)
    # Inspect each listed file before confirming.
    return input("Type EXECUTE after review: ") == "EXECUTE"

result = run_program1_with_feedback(
    GeminiSASTranslator(), review,
    run_dir="output/program1_feedback/run_001",
)
print(result["status"])
```

Use the isolated environment described under Safety. Review is synchronous:
any return value other than `True` stops the run with `REVIEW_REQUIRED` before
that candidate executes. The loop cannot resume a declined or interrupted run.

Reusing `run_dir` raises `FileExistsError` to protect earlier artifacts. To
restart the loop, choose a new directory and review the newly generated code.
You can execute saved, reviewed files with `run_reviewed_program1(code_dir=...)`,
but this does not resume the loop or restore its in-memory reports.

#### Repairs and stopping conditions

For numerical or structural validation failures, `translator.repair_program1()`
receives complete failed-output diagnostics, the code that produced them, and
the original SAS and measure configuration. The code comes from the completed
execution report, so later working-file edits cannot change the repair request.
Repaired files preserve the exact UTF-8 code returned by the repair request.

Repairs include the shared macro and failed group steps. After review, all five
groups run and validate again because macro changes can affect every group.

- `max_attempts` defaults to 3 and includes the initial translation. A passing
  run stops immediately; reaching the limit returns the final `FAIL` result.
- Runtime errors and blocked steps stop further repairs, even when other groups
  have validation mismatches.
- References with `duplicate_sas_ids` or `blank_sas_keys` are listed in
  `non_repairable_outputs`. Their diagnostics remain in `outputs` and `attempts`,
  but are excluded from repair feedback and group repair requests. Other failed
  groups can still be repaired. If only invalid references remain, the loop
  stops with `FAIL`.
- Generation retries each step independently; repair requests have a separate
  retry limit. Both allow up to three requests for transient API or network
  failures, with exponential backoff starting at two seconds. Retrying a request
  does not count as an additional validation attempt.

The loop uses the finalized #40 validation tolerances (`rtol=1e-9`,
`atol=1e-12`). It handles Program 1; Programs 0 and 2 use separate orchestration.
Existing lower-level entry points remain available.

#### Reports and errors

All outcomes (`PASS`, `FAIL`, `REVIEW_REQUIRED`, `ERROR`) use the same report
fields:

| Field | Meaning |
|---|---|
| `status`, `passed` | Overall outcome; `passed` is true only for `PASS` |
| `attempt` | Current candidate number, including candidates not executed |
| `attempts` | Completed execution/validation reports, each with its own `attempt` and `code_dir` |
| `steps`, `outputs` | Latest completed report, or empty before any execution completes |
| `code_dir` | Current candidate directory; it may be absent or partial after generation/repair failure |
| `error` | `None`, or wrapper error details: `stage`, candidate `attempt`, exception `type`/`message`, request `attempts`, and `cause_type` |

Failed API requests are not added to `attempts`. If a new candidate fails before
execution completes, `steps` and `outputs` retain the last completed report.
For repair errors, `error["attempts"]` counts all translator calls, including
transient failures before a malformed reply or permanent error. Other stages
use the retry exception's count when available, otherwise 1.

Malformed replies, permanent API errors, exhausted retries, review callback
exceptions, and file-operation errors return `ERROR` while retaining completed
reports. Errors collected by the execution pipeline remain in its `steps` and
`outputs` with overall `FAIL`.

Invalid arguments/configuration and initial run-directory creation raise before
work starts. Lower-level functions keep their existing exception behavior.
Process interruptions such as `KeyboardInterrupt` are not caught.

#### Saved files and recovery

Code and CSVs are kept in separate `attempt_1/`, `attempt_2/`, etc. directories.
Reports remain in memory; durable report storage and loop resumption are not
implemented. Inspect the reports and partial files before starting another run.

Git ignores the default `output/program1_feedback/` tree. Keep custom runs under
that tree or outside the repository, or add appropriate ignore rules.

### Lower-level retries and reporting

Lower-level translation (without the feedback wrapper):
- Temporary API failures (429, 5xx) and network errors are retried.
- Maximum of 3 attempts, with exponential backoff starting at 2 seconds.
- Permanent errors are raised immediately without retrying.
- Exhausted transient retries raise `RetryFailure` with attempt information.

Lower-level validation (without the feedback wrapper):
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
