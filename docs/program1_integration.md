# Program 1 integration

Program 1 connects Gemini translation, Python execution, and SAS output
validation. Generate the shared macro and five group steps, review the Python
files, then execute them.

Issue #42 adds persistent history to this workflow. See
[Translation attempt history](translation_history.md) for reading results,
choosing a history location, and troubleshooting storage.

## Generate, review, and execute

Run these examples from the repository root after installing the project
dependencies. Set `GEMINI_API_KEY` in the environment or a local `.env` file for generation;
never commit the key. An exported value takes precedence over `.env`.

### 1. Generate code

```python
from src.program1_integration import generate_program1_code
from src.translator import GeminiSASTranslator

files = generate_program1_code(GeminiSASTranslator())
for step_id, path in files.items():
    print(step_id, path)
```

The CLI also generates code without executing it:
`python -m src.program1_integration --code-dir output/program1_reviewed_code`.

Generation saves code to `output/program1_reviewed_code/` by default and requires
an empty or new directory. It neither executes nor reviews the generated code.

### 2. Review all six Python files

Review `grp_score_macro.py` and all five group files before execution. See
[candidate history](translation_history.md#candidates-review-and-repeated-executions)
for how review edits and original snapshots are preserved.

Generated Python runs through `exec()`, which is not a sandbox. Execute only
reviewed code in a disposable, isolated environment without accessible secrets
or unrelated sensitive files. Removing an API key from the environment alone
does not provide isolation. In the feedback loop, generated code runs in the
same process as the Gemini client holding the API key, so review every
candidate, including repairs, and reject code that hard-codes SAS reference
values. Removing an environment variable does not remove a key the client
already holds.

### 3. Execute reviewed code

In the execution environment, use the same code directory and its history:

```python
from src.program1_integration import run_reviewed_program1

result = run_reviewed_program1(on_error="report")
print(result["status"])
for table, output in result["outputs"].items():
    print(table, output["status"])
```

The function trusts the caller to have reviewed the code and makes no Gemini
requests. By default, it reads `STD_DATA_2025JUL_ANALYSIS.csv` from
`data/Project_1/SAS Output CSV/`, validates against the group references there,
and writes CSVs to `output/data/`.

Use `code_dir`, `input_csv`, `output_dir`, `manifest_path`, `config_path`, and
`sas_output_dir` to select other paths. Keep the manifest and configuration the
same between generation and execution unless changing them intentionally.
History records the configuration used for each execution separately from
generation metadata.

## Optional validation feedback loop

`run_program1_with_feedback()` from issue #41 / PR #54 is available in this
combined branch. It generates, reviews, executes, and validates candidates, then
asks Gemini to repair validation mismatches within a fixed attempt limit. It
handles Program 1 only; Programs 0 and 2 use separate orchestration.

```python
from src.program1_integration import run_program1_with_feedback
from src.translator import GeminiSASTranslator

def review_candidate(files):
    for step_id, path in files.items():
        print(step_id, path)
    # Open and inspect every listed file before approving execution.
    return input("Type EXECUTE after reviewing these files: ") == "EXECUTE"

result = run_program1_with_feedback(
    GeminiSASTranslator(),
    review_candidate,
    max_attempts=3,
    run_dir="output/program1_feedback/run_001",
)
print(result["status"])
```

The same isolated-execution requirements apply. The wrapper provides no sandbox;
the review callback must arrange actual review, not automatically approve code.

### Review, repair, and stopping conditions

- Use a new `run_dir` for each run. Reusing one raises `FileExistsError`;
  candidate code and CSVs are separated into `attempt_1/`, `attempt_2/`, etc.
- The callback receives step IDs mapped to candidate paths on every attempt.
  Only the literal return value `True` allows execution. Review is synchronous:
  any other return value stops with `REVIEW_REQUIRED`; it does not pause the run.
- `max_attempts` includes the initial candidate. A passing run stops immediately;
  reaching the limit returns the final `FAIL`.
- On validation failure, `translator.repair_program1()` receives failed-output
  diagnostics and the Python code that was actually executed, taken from the
  completed execution report, so later edits to working files cannot change the
  repair request. It also receives the original SAS and measure configuration.
  Repaired files are saved as the exact UTF-8 bytes the repair returned, with no
  newline conversion. Repairs include the shared macro and failed group steps. After
  review, all five groups run again to detect regressions from macro changes.
- References with `duplicate_sas_ids` or `blank_sas_keys` are listed in
  `non_repairable_outputs`. Their diagnostics remain in the reports, but they
  are excluded from feedback and group repair requests. Other groups can still
  be repaired; if only invalid references remain, the loop stops with `FAIL`.
- Runtime errors and blocked steps stop the loop. They are not treated as
  validation mismatches. Transient repair-request failures use separate bounded
  API retries, up to three requests with backoff starting at two seconds.

The loop cannot resume a declined review or interrupted run. You can execute
saved, reviewed files with `run_reviewed_program1(code_dir=...)`, but doing so
does not resume the loop or restore its in-memory history. Restart the full
loop in a new run directory and review its newly generated code.

### Feedback report and errors

| Field | Meaning |
|---|---|
| `status`, `passed` | Overall `PASS`, `FAIL`, `REVIEW_REQUIRED`, or `ERROR`; `passed` is true only for `PASS` |
| `attempt` | Current candidate number, including candidates not executed |
| `attempts` | Completed pipeline reports, including reports with step errors, with their own attempt number and code directory |
| `steps`, `outputs` | Latest completed report, or empty before any execution completes |
| `code_dir` | Current candidate directory; it may not exist or may be partial after generation/repair failure |
| `error` | `None`, or wrapper error details: `stage`, candidate `attempt`, exception `type`/`message`, request `attempts`, and `cause_type` |

An error before the current candidate completes does not replace the last
completed `steps` and `outputs`. Failed API requests are not validation attempts.
For repair errors, request counts include all translator calls, even when a
transient failure is followed by a malformed reply or permanent error. Other
stages use the retry exception's count when available, otherwise 1.

Malformed replies, permanent API errors, exhausted repair retries, review
callback exceptions, and file-operation errors return `ERROR` while retaining
completed reports. Errors collected by the execution pipeline remain in its
`steps`/`outputs` with overall `FAIL`. Invalid arguments/configuration and
initial run-directory creation still raise before work starts; process
interruptions such as `KeyboardInterrupt` are not caught.

### Feedback history limits

The wrapper returns its complete `attempts` list in memory. Its calls to the
generation and reviewed-execution functions also create local history, but
repair requests and candidates awaiting review do not yet have complete
persistent coverage. See [feedback-loop history limits](translation_history.md#branch-scope-and-related-work).
Do not use local JSON alone to infer the full loop outcome.

Only the default `output/program1_feedback/` tree is covered by the feedback
output ignore rule. Keep custom runs beneath that tree or outside the repository,
or add appropriate ignore rules.

## Results and error handling

The following describes separate reviewed execution; the wrapper's overall
statuses and error handling are described above.

The report contains overall `status` and `passed`, step reports in `steps`, and
table results in `outputs`. Validated outputs include comparison details.

- `on_error="raise"` is the default; step exceptions are raised after their
  outcomes are saved.
- `on_error="report"` collects step errors with the stage, exception type, and
  message. Independent groups can continue; steps whose dependencies failed are
  marked `BLOCKED`.
- Validation mismatches produce `FAIL`. They do not regenerate code or
  automatically block downstream execution.
- Configuration and input checks, output-directory creation, and stale-output
  cleanup can raise errors in either mode. Once an execution has a history
  record, setup errors are saved too. History storage errors also raise.

Stale group CSVs are removed before execution. A failed run can still leave fresh
CSVs from completed steps; use the report and history to interpret them.

### Retries and recovery

Generation makes up to three requests per step by default when transient API or
network failures occur, using exponential backoff starting at two seconds.
Permanent errors raise immediately; exhausted retries raise `RetryFailure` with
the attempt count and original error. `max_attempts` and `retry_delay_seconds`
configure generation retries.

Temporary validation errors are retried up to three attempts without regenerating
or re-executing code. Validation mismatches are reported directly by the
lower-level runner; the optional feedback wrapper handles code repair.

After interrupted or failed generation, inspect the partial files and history,
fix the cause, and generate into a new directory. Review the new code before
execution. Re-executing reviewed code preserves history but replaces its working
output CSVs.

## Validation references and recorded results

The original CSV references were rounded, which caused correct code to fail
validation (#40). They are archived in
`data/Project_1/archive/original_rounded_csv/`. The current references in
`SAS Output CSV/` contain exact values from the `.sas7bdat` files. The validator
defaults to `rtol=1e-9` and `atol=1e-12`.

With these references, one recorded Gemini run passed all five Program 1
outputs, with a largest difference from SAS of `1.07e-14`. Each output had
4,566 rows with matching column order and provider IDs. These results describe
one run, not guaranteed accuracy for future translations.

If validation fails, inspect the failing rows rather than raising the tolerance.
Issue #42 does not change validation tolerances.

## Implementation and tests

| File | Responsibility |
|---|---|
| `src/orchestrate.py` | Execution plans, dependencies, and reports for Programs 0, 1, and 2 |
| `src/translator.py` | Gemini translation and the separate legacy runner |
| `src/program1_integration.py` | Generation and reviewed execution |
| `src/validate.py` | SAS output comparisons |
| `src/translation_history.py` | Candidate identity and local JSON history |

`tests/test_program1_integration.py` exercises the workflow with fake translators
and synthetic references. Storage and legacy-runner coverage is described in the
[history guide](translation_history.md#maintenance-and-scope).
