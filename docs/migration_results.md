# Demo measurements

Read [demo/results.json](../demo/results.json) for the Issue #44 charts. The file uses schema version 1. Each entry in `runs` is one observation. Use its `source` and `time_scope` to choose comparable runs.

The tested setup for live measurements is Windows PowerShell with Ubuntu WSL and Docker. Reading the JSON and building charts requires neither Docker nor a Gemini API key. The file contains three live observations and two offline comparisons.

| Live run | First-try steps | Repairs | SAS validation |
| --- | ---: | ---: | --- |
| Program 0 | 33/33 | 0 | Two passed. `LESS100_MEASURE` failed because the empty table's two columns were reversed. |
| Program 1 | 6/6 | 0 | All five passed. |
| Program 2 | 13/23 known, eight unknown | 16 | The run ended in `FAIL` after runtime errors, including list arguments to the single-column `fastclus` helper. Both SAS outputs were `NOT_CHECKED`. |

The reviewed Gemini fixture and handwritten baseline each passed all ten SAS checks. Their saved replay times were 1.671 and 0.320 seconds, respectively, with zero API cost. Use those two rows to compare local execution and validation times. Live times also include generation and Docker staging, with human review time excluded.

## Chart fields

| Run fields | How to use them |
| --- | --- |
| `id`, `source`, `programs`, `implementation` | Identify the observation and its scope. |
| `configuration` | Recorded model name, prompt version, column context, repair loop and FASTCLUS helper settings. Some fixture chains also have `model_name_by_program`. |
| `status`, `elapsed_seconds`, `time_scope`, `cost_usd` | Show the terminal result, time in seconds and attributable cost. Live billed cost is unknown. |
| `first_try_passed`, `first_try_measured`, `first_try_success_rate` | The denominator counts steps with known first-attempt outcomes. The rate ranges from 0 to 1 and is null for an empty denominator. |
| `steps[]` | Each row gives `id`, `status`, `first_try_success` and `fix_attempts`, plus `program` for multi-program runs. The handwritten baseline has no translation steps. |
| `outputs` | Keyed by SAS table name. Each entry gives `program`, `status` and `largest_difference`. `NOT_CHECKED` means no valid SAS comparison was available. |

Display JSON `null` as unknown, while `0` indicates a recorded measurement of zero. For Program 1, a first-try success requires a passing validation for the original group candidate or successful execution of the original macro. Programs 0 and 2 report step execution and SAS table correctness separately.

The exporter counts one fix per targeted step for each logical repair request. A dispatched repair still counts if it fails or its replacement is declined. A recorded rejection before the first POST adds zero fixes. If the cap stops a retry after dispatch, the original repair still counts once. Older histories retain their recorded counts unless they explicitly show the repair was stopped before dispatch.

Keep `historical_observations` separate from individual runs. The README documents five historical Program 1 runs that passed all five outputs. Their total attempt count is unknown (`gemini_runs: null`), and their first-attempt outcomes are unavailable. Exclude that summary from the new live run's first-try rate.

## Reproducing measurements

### 1. Prerequisites

The tested setup uses Windows PowerShell with Python 3.11, the project dependencies and google-genai 3.6.1. The Windows launcher specifically calls `wsl.exe -d Ubuntu`. That distribution needs access to a local Linux Docker engine at `/var/run/docker.sock` and the Windows temporary directory.

Build the validator from the repository root **in Ubuntu WSL**:

```bash
docker --host unix:///var/run/docker.sock build -t issue43-program1:local - < docker/validation.Dockerfile
```

From the measurement worktree **in PowerShell**, run both suites with the installed image. Require zero failures and zero skips before using a key.

```powershell
$env:ISSUE43_TEST_DOCKER = '1'
python -B -m unittest -v tests.test_measure_program1 tests.test_measure_steps
if ($LASTEXITCODE -ne 0) { throw 'Measurement tests failed' }
```

### 2. Running Program 1

For a new paid or quota-consuming run, supply `GEMINI_API_KEY` privately in a dedicated PowerShell session. Use a temporary process environment variable and keep the key out of repository files and command history.

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:GOOGLE_GENAI_HTTP_CLIENT = 'httpx'
$run = "output/program1_feedback/measured-$([guid]::NewGuid())"
try {
    python -B -m src.measure_program1 --run-dir "$run"
} finally {
    Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
}
```

Open every listed Python file, including repairs, before typing `RUN`. Any other answer ends the run with `REVIEW_REQUIRED`. The measured client allows at most 24 outbound generation POSTs across six initial translations, up to two batched repair rounds and application retries. SDK continuation and redirects are disabled. An incomplete response stops the run.

### 3. Inspecting and exporting results

From PowerShell, use the completed run directory as `$run`. Export to a **new file** in that directory to preserve the checked-in observation. This command joins the saved history and replays the two offline chains:

```powershell
$candidate = Join-Path $run 'consolidated-results.json'
if (Test-Path -LiteralPath $candidate) { throw 'Candidate export already exists' }
python -B -m src.migration_results --program1-run "$run" --replay-reviewed-fixtures --output "$candidate"
if ($LASTEXITCODE -ne 0) { throw 'Results export failed' }
```

Omit `--replay-reviewed-fixtures` for a live-only export. The exporter also accepts `--history PROGRAM STORE RUN_ID` for an existing history store. Always specify `--output` when experimenting.

For a Program 1 feedback directory with linked #66 histories, `--program1-run`
also works without `measurement.json`. It counts repair targets across attempts
and uses the original candidates for first-try outcomes. Unmeasured timing and
cost remain null. Passing a linked attempt to `--history 1 STORE RUN_ID` reads
the same whole loop using history alone. Keep the attempt directories together.
An incomplete execution cannot inherit an earlier pass.

**Optional diagnostics:** See `measurement.json` in the run directory for detailed timing, API usage, and repair records. Keep it alongside the saved attempts. Generated code and raw history remain local.

### 4. Evidence interpretation and limitations

- The measured path runs approved Python in a disposable, non-root Docker container with networking disabled, read-only inputs and private scratch space. Docker failures stop the run. Candidate code shares a process with the worker and can read staged SAS references in Program 1. Review for hard-coded answers and verify saved CSVs independently when accepting a result.
- The parent checkpoints each request before dispatch. A reserved slot has unknown dispatch outcome. A completed slot means the SDK returned a response. Missing usage is null. A hard interruption can leave an INCOMPLETE checkpoint and partial history. Finalized exports keep the last completed validation alongside the terminal run status and attempted repairs.
- Live run `db1be915acf34d03b990daac6f6898ae` used `gemini-3.5-flash-lite` with repairs enabled. Active time was 9.46 seconds and human review took 263.57 seconds. Local evidence records 2,952 input and 999 output tokens. The supplied Standard paid-tier equivalent is approximately $0.003383. Actual billing is unverified, so the consolidated file keeps `cost_usd` null.
- The translator source hash identifies the prompt version. The saved live runs use the same model. Controlled comparisons of model, prompt, column context, repair-loop or FASTCLUS settings require additional runs.

### 5. Collecting Programs 0 and 2

Use the measured entry point below for isolated execution with the repair history introduced in [PR #103](https://github.com/Break-Through-Tech/Consulting-1D-ai-powered-sas-migration/pull/103). The ordinary step runner executes reviewed code on the host.

After the offline tests pass and the request budget is approved, run the following from a dedicated PowerShell session. Program 0 uses the original input. Program 2 starts from the six SAS-produced upstream tables so it can be evaluated independently. Its two target SAS references stay on the host, as do Program 0's three references.

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:GOOGLE_GENAI_HTTP_CLIENT = 'httpx'
$p0 = "output/step_measurements/p0-$([guid]::NewGuid())"
$p2 = "output/step_measurements/p2-$([guid]::NewGuid())"

function Invoke-StepMeasurement($program, $directory) {
    $secret = Read-Host 'Gemini API key' -AsSecureString
    try {
        $env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
        python -B -m src.measure_steps $program --run-dir "$directory" --request-limit 50
        if ($LASTEXITCODE -ne 0) { throw "Run stopped. Inspect $directory before continuing." }
    } finally {
        Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
        $secret.Dispose()
    }
}
Invoke-StepMeasurement 0 $p0
```

Each run requires a fresh directory. The model receives step SAS, manifest notes, dataset names, macro variables and function signatures. A repair can also send source and error text containing input values. Read the displayed feedback before approving transmission. Review each executable candidate separately.

The hard ceiling is 50 generation POSTs per invocation, including transport retries (100 across these two invocations). A clean first pass takes 33 for Program 0 or 31 for Program 2. Syntax and runtime fixes share a three-candidate limit per step. SDK retries, redirects and automatic continuation are disabled. An incomplete response stops the run.

`--min-interval SECONDS` defaults to 5 seconds between generation POST starts, including failed requests, retries and repairs. This paces one invocation at 12 requests per minute, below the project's 15 RPM limit. Use a longer interval if the project needs more headroom. Traffic from other clients and the project's token and daily limits can still cause HTTP 429. Free-tier content is eligible for product improvement. Standard paid pricing for `gemini-3.5-flash-lite` is $0.30 per million input tokens and $2.50 per million output tokens including thinking. Actual charges depend on tokens and billing tier. [Pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite) · [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)

The authenticated parent verifies and stages approved code. The container gets a read-only copy of the required inputs, private scratch space, and no external network. The host checks returned CSVs against its own SAS references. Generated code shares the step runner's Python process inside the container and can affect its reported step outcomes. Review candidates for hard-coded answers or interference with the runner.

### 6. Inspect each run offline

In the same PowerShell session, check Program 0's request ledger, history and saved CSVs against SAS. A failed validation is a measured result. A run stopped before terminal validation retains its partial evidence for inspection.

```powershell
python -B -m src.verify_step_measurement 0 "$p0"
if ($LASTEXITCODE -ne 0) { throw 'Review Program 0 evidence before continuing' }
```

After reviewing Program 0, collect and check Program 2:

```powershell
Invoke-StepMeasurement 2 $p2
python -B -m src.verify_step_measurement 2 "$p2"
if ($LASTEXITCODE -ne 0) { throw 'Review Program 2 evidence before exporting' }
```

Export both verified observations to a new file. The original Program 1 row, historical observations and replay timings carry forward unchanged.

```powershell
$candidate = "output/step_measurements/consolidated-$([guid]::NewGuid()).json"
python -B -m src.migration_results --append-to demo/results.json --step-run "$p0" --step-run "$p2" --output "$candidate"
if ($LASTEXITCODE -ne 0) { throw 'Candidate export failed' }
```

`measurement.json` records the configured interval and separates review time from active time, which includes pacing and retry waits. Per-request elapsed time in `requests.json` includes any pacing wait. The ledger preserves returned token usage. A reserved slot has unknown dispatch status. Missing usage and unverified billing remain unknown. Keep run directories local and review the candidate before replacing the demo file.
