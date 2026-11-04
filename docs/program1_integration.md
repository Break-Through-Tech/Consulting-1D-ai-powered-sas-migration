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
- Code-generation failures can leave partially generated files;
  inspect the failure and use a new output directory when retrying.
- The lower-level integration functions still raise configuration and
  pre-execution file errors. The feedback wrapper reports operational errors
  as described below; invalid configuration and run-directory setup errors
  still raise before a run starts.
- Numerical parity with all five SAS outputs is not yet confirmed.

## Retry and Error Handling

### Validation feedback repair (issue #41)

`run_program1_with_feedback(translator, review_code, max_attempts=3,
run_dir=...)` is an opt-in wrapper around the existing integration.
Use a new run directory each time. It generates the initial six files,
calls `review_code(files)` with step IDs mapped to candidate paths, and
executes only when the callback returns exactly `True`. The callback must
arrange actual review; do not automatically approve untrusted model output.
Execution still requires the isolated environment described above.

Review is synchronous: the callback must complete review and return `True`
within the same call. Returning anything else stops the run with
`REVIEW_REQUIRED`; it does not pause it. Resumable review is not supported.
Calling the wrapper again with the same run directory raises `FileExistsError`
to protect earlier artifacts. You may execute saved, reviewed files separately
with `run_reviewed_program1(code_dir=...)`, but this does not resume the feedback
loop or restore its attempt history. To restart the full loop, choose a new
run directory and review the newly generated code again.

On numerical or structural validation failure, the wrapper sends the existing
validator's complete failed-output diagnostics and previous Python source to
`translator.repair_program1()`. Gemini receives the original SAS and measure
configuration as well. The correction includes the shared macro and failed
group steps, because a defect in the macro can affect every group. All five
groups are re-executed and revalidated after review of each correction.

Outputs with `duplicate_sas_ids` or `blank_sas_keys` are classified as
non-repairable reference-data failures and listed in `non_repairable_outputs`
on each completed report. Their original diagnostics remain in `outputs` and
`attempts`, but they are excluded from Gemini feedback and group repair requests.
Other failed groups can still be repaired and revalidated. If only invalid
reference outputs remain, the loop stops with FAIL without another repair call.

`max_attempts` includes the initial translation. A passing run stops immediately;
an exhausted run returns its final FAIL result. Runtime errors and blocked
steps stop the correction loop rather than being treated as numeric mismatches.
Transient API failures retain the existing bounded network retries.
Review refusal returns `REVIEW_REQUIRED` without executing that candidate.
The returned `attempts` list contains validation reports for executed attempts;
code and CSV outputs are separated under `attempt_1/`, `attempt_2/`, etc.
Existing entry points and validation tolerances are unchanged.

The feedback wrapper returns the same report envelope for PASS, FAIL,
REVIEW_REQUIRED, and ERROR:

- `status` and `passed` describe the overall outcome.
- `attempt` is the current candidate number, including an unexecuted candidate.
- `attempts` contains only completed execution/validation reports, each with its
  own `attempt` number and `code_dir`. It never adds a failed API request as a
  validation attempt.
- `steps` and `outputs` retain the latest completed execution/validation report,
  or are empty when no execution has completed. They describe the last completed
  candidate, not a correction that failed before execution.
- `code_dir` identifies the current candidate's directory. It may be absent or
  partially populated after a generation or repair failure.
- `error` is `None` unless an operation raises. In that case, status is ERROR
  and `error` contains `stage`, candidate `attempt`, exception `type`, `message`,
  request `attempts`, and `cause_type`. For repair failures, request attempts are
  counted as the translator is invoked, including transient failures before a
  malformed response or permanent API error. Other stages use the retry
  exception's count when available, otherwise 1.

Malformed Gemini replies, permanent API errors, and exhausted transient repair
retries stop the loop with ERROR while preserving every completed report.
Generation, review callback, and file-operation exceptions use this same
envelope. Errors already reported by the existing execution pipeline remain
in the completed report's `steps` and `outputs`, with overall status FAIL.
Argument/configuration validation and initial run-directory creation remain
preflight exceptions. The lower-level functions keep their existing exception
interfaces; process interruptions such as KeyboardInterrupt are not swallowed.

Only the default `output/program1_feedback/` tree is ignored by Git. Keep
custom run directories inside that tree or outside the repository; arbitrary
custom directories elsewhere in the repository are not automatically ignored.
Reports are returned in memory; durable report storage and resume support
remain separate work.

This implements the bounded correction mechanism discussed in #35 using the
current validator contract. Future diagnostic additions from #39 will flow
through automatically. It does not select new numeric tolerances (#40),
implement general Programs 0/2 orchestration (#49), or add a history database.

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
