# Demo measurements

Read [demo/results.json](../demo/results.json) for the Issue #44 charts. The file uses schema version 1. Each entry in `runs` is one observation. Use its `source` and `time_scope` to choose comparable runs.

Teammates can read the JSON and build the charts without Docker or a Gemini API key. Reproducing the isolated live Program 1 measurement requires Docker and Gemini access.

| Source | What the saved run measures |
| --- | --- |
| `translation_history` | Live Program 1 generation, execution and SAS validation, excluding human review. Six of six original steps passed, with zero repairs. |
| `reviewed_fixture` | Local replay of reviewed Gemini code for Programs 0, 1 and 2. All ten SAS tables passed in 1.671 seconds. |
| `handwritten_baseline` | Local execution of the handwritten chain on the same inputs. All ten SAS tables passed in 0.320 seconds. |

The two replays incur zero API cost. Their times cover execution and validation, including each runner's file and history overhead. The live time also includes generation and Docker staging. Use the two replay rows for the chain timing comparison.

## Chart fields

| Run fields | How to use them |
| --- | --- |
| `id`, `source`, `programs`, `implementation` | Identify the observation and its scope. |
| `configuration` | Recorded model name, prompt version, column context, repair loop and FASTCLUS helper settings. Some fixture chains also have `model_name_by_program`. |
| `status`, `elapsed_seconds`, `time_scope`, `cost_usd` | Show the terminal result, time in seconds and attributable cost. Live billed cost is unknown. |
| `first_try_passed`, `first_try_measured`, `first_try_success_rate` | The denominator counts steps with known first-attempt outcomes. The rate ranges from 0 to 1 and is null for an empty denominator. |
| `steps[]` | Each row gives `id`, `status`, `first_try_success` and `fix_attempts`, plus `program` for multi-program runs. The handwritten baseline has no translation steps. |
| `outputs` | Keyed by SAS table name. Each entry gives `program`, `status` and `largest_difference`. |

Display JSON `null` as unknown. A measured zero is meaningful. For Program 1, a first-try success requires a passing validation for the original group candidate or successful execution of the original macro. Programs 0 and 2 report step execution and table correctness separately. One logical repair counts once per targeted step, even if the request fails or review is declined. Transport retries do not add fixes. Programs 0 and 2 need recorded repair evidence from #66.

Keep `historical_observations` separate from individual runs. The README documents five historical Program 1 runs that passed all five outputs. Their total attempt count is unknown (`gemini_runs: null`), and their first-attempt outcomes are unavailable. Exclude that summary from the new live run's first-try rate.

## Measure a live Program 1 run

### 1. Prerequisites

The tested setup uses Windows PowerShell with Python 3.11, the project dependencies and google-genai 3.6.1. The Windows launcher specifically calls `wsl.exe -d Ubuntu`. That distribution needs access to a local Linux Docker engine at `/var/run/docker.sock` and the Windows temporary directory.

Build the validator from the repository root **in Ubuntu WSL**:

```bash
docker --host unix:///var/run/docker.sock build -t issue43-program1:local - < docker/program1.Dockerfile
```

From the same checkout **in PowerShell**, verify the installed image and measurement flow with offline tests:

```powershell
$env:ISSUE43_TEST_DOCKER = '1'
python -B -m unittest discover -s tests -p test_measure_program1.py -v
if ($LASTEXITCODE -ne 0) { throw 'Measurement tests failed' }
```

### 2. Running a measurement

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

- The measured path runs approved Python in a disposable, non-root Docker container with networking disabled, read-only inputs and private scratch space. Docker failures stop the run. Candidate code shares a process with validation and can read SAS references. Review for hard-coded answers and verify saved CSVs independently when accepting a result.
- The parent checkpoints each request before dispatch. A reserved slot has unknown dispatch outcome. A completed slot means the SDK returned a response. Missing usage is null. A hard interruption can leave an INCOMPLETE checkpoint and partial history. Finalized exports keep the last completed validation alongside the terminal run status and attempted repairs.
- Live run `db1be915acf34d03b990daac6f6898ae` used `gemini-3.5-flash-lite` with repairs enabled. Active time was 9.46 seconds and human review took 263.57 seconds. Local evidence records 2,952 input and 999 output tokens. The supplied Standard paid-tier equivalent is approximately $0.003383. Actual billing is unverified, so the consolidated file keeps `cost_usd` null.
- The translator source hash identifies the prompt version. Comparing model, prompt, column context, repair-loop or FASTCLUS settings requires additional measured configurations. The checked-in live observation covers one configuration.
