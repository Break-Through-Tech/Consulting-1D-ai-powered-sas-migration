# Translation attempt history

Program 1 already saved generated Python and output CSV files. Issue #42 adds
persistent history: code snapshots in unique directories and local JSON records
linking candidates to execution results, errors, and metadata. The team uses
local JSON storage; no additional dependencies are required.

## Use and inspect history

Follow the [generate, review, and execute workflow](program1_integration.md).
History is saved automatically under the selected code directory:

```text
<code_dir>/
  <step_id>.py                 working files for review
  translation_history.json   current run and candidate references
  .translation_history/
    <run UUID>/
      <record UUID>/
        record.json           metadata, IDs, timestamps, and record payload
        code.py               candidate code snapshot, when present
```

The default `code_dir` is `output/program1_reviewed_code/`. Read its history
without calling Gemini or executing generated code:

```python
import json
from pathlib import Path
from src.translation_history import LocalJSONHistoryStore

code_dir = Path("output/program1_reviewed_code")
pointer = json.loads(
    (code_dir / "translation_history.json").read_text(encoding="utf-8")
)
store = LocalJSONHistoryStore(code_dir / ".translation_history")
records = store.read_run(pointer["run_id"])
candidates = {r["record_id"]: r for r in records if r["type"] == "candidate"}

for record in records:
    if record["type"] == "outcome":
        candidate = candidates[record["candidate_id"]]
        print(record["execution_id"], candidate["step_id"], record["result"]["status"])
        # candidate["code"] contains the exact saved source.
        # record["result"] contains validation details or the step error.
    elif record["type"] == "execution_error":
        print(record["execution_id"], record["operation"], record["error"])
```

This example reads reviewed-execution records. The legacy runner uses a different
format, described below.

### Choose another history location

Create a store, for example `store = LocalJSONHistoryStore("output/history")`,
then pass `history_store=store` to both `generate_program1_code()` and
`run_reviewed_program1()`. Use the same location for later reads. The pointer
file stores IDs and hashes, not the history location.
Its run must exist in the selected store; missing or corrupt history raises an
error rather than starting over.

Git ignores `.translation_history/` directories and `translation_history.json`
pointer files. Add ignore rules for custom history roots and working Python/CSV
directories inside the repository if existing rules do not cover them.

## Candidates, review, and repeated executions

A translation attempt is one Python candidate returned for one step. Failures
that return no code (API, network, or response parsing) produce `request_error`
records, with request numbers per step. Generation saves empty strings with a
`generation_error`; non-string responses have no code snapshot.

Generation saves a snapshot before writing the working `.py` file. Candidates
include the step's SAS code and configuration, a SHA-256 code hash, and
`review_status="pending"`. The run records the model name when available, leaving
unknown metadata absent. It does not save the translator client, credentials, or
environment.

`run_reviewed_program1()` records the caller's review declaration as
`caller_supplied_reviewed_code`; it does not verify approval or identify a
reviewer. Original candidate records are never updated. For reused candidates,
the review declaration is saved in the execution record. There is no review UI
or way to pause and resume review.

- Unchanged code reuses its candidate ID. Reads, writes, and hashes preserve
  exact UTF-8 content, including LF/CRLF line endings.
- Edits are detected when execution starts and saved as candidates linked by
  `previous_candidate_id`. Earlier code and results remain available.
- Reviewed files without history are imported with origin `reviewed_file`;
  unknown generation/model metadata stays absent.
- Re-executing unchanged files adds execution results, not candidates. Separate
  generation requests remain separate candidates even if their code is identical.

Execution uses a snapshot of all six files held in memory. Later file edits
cannot change which code runs or which candidate receives its results.

## Execution records and failures

Each reviewed execution has a unique `execution_id`. Its `execution_started`
record lists the candidate IDs, including the shared macro, and saves the
`execution_plan` used to run them. This preserves each execution's measure
settings even if the configuration file later changes at the same path.
Generation metadata stays separate.

| Record type | Contents |
|---|---|
| `run` | Available generation/import metadata |
| `candidate` | Step metadata, code hash, origin, review status, and optional previous candidate ID |
| `request_error` | Step ID, request number, and exception details without returned code |
| `generation_error`, `artifact_error` | Invalid response or working-file export failure |
| `execution_started` | Execution ID, candidate mapping, review declaration, plan, and input/configuration paths |
| `outcome` | Candidate/execution IDs, validation status, and step result |
| `execution_error` | Execution ID, setup operation/path, and exception details |

Outcomes save validation details and step errors, including when
`on_error="raise"`. Macro validation is `not_applicable`; group validation is
`completed` when a result exists and `not_completed` otherwise. The step result
distinguishes validation `PASS`/`FAIL`, runtime `ERROR`, and `BLOCKED`.

Configuration and input checks can raise before an `execution_started` record is
saved. After that record exists, output-directory creation and stale-output
cleanup failures save an `execution_error` before re-raising, even with
`on_error="report"`. History write errors also raise. If saving a failed
translator request fails, `HistoryStorageError` prevents the storage error from
triggering another model request.

### Legacy translator workflow

`src.translator.run_translator()` saves candidates, request errors, and
execution/export outcomes. It executes without a review gate and does not
validate, so records use `review_status="not_recorded"` and
`validation_status="not_run"`. Outcomes have top-level `status` and optional
`error` fields rather than the reviewed runner's nested `result`.

Each call starts a new history run while reusing working Python/CSV filenames.
Earlier history remains available. This runner does not save execution IDs or
configuration-plan snapshots.

## Interrupted writes and storage limits

Records are append-only, with `schema_version=1` and UTC `recorded_at` timestamps.
Each record is written to a temporary directory, then renamed into place.
Readers ignore unfinished temporary directories. Pointer files are replaced
through temporary files. Reads verify code hashes and raise on corrupt records.
Timestamps reflect when each record was saved.

- After interrupted generation, inspect partial files and history, then generate
  into a new code directory. There is no automatic resume.
- An interrupted pointer update can leave a saved candidate unlisted in the
  pointer file. `read_run()` still returns it. If the initial pointer was not
  saved, look for the run UUID directory under the history root.
- An `execution_started` record alone does not prove completion. Missing outcomes
  may indicate an interruption or storage failure; do not assume the run passed.
- Keep pointers and their history together when moving or archiving code.

Use one writer per code directory. Atomic writes prevent partial JSON reads, but
record and pointer updates are separate operations. Writes are not guaranteed to
survive power loss (`fsync` is not used), and shared-filesystem behavior is not
guaranteed.
There is no automatic cleanup, retention policy, indexing, or cross-run query
service. Validator examples and error messages are saved as returned; consider
their contents when deciding who can access history and how long to keep it.

### JSON values

NumPy scalar numbers and booleans retain their JSON types. Other supported
values are represented as follows:

| Python value | Stored JSON |
|---|---|
| pandas missing value (`pd.NA`, `pd.NaT`) | `{"__type__": "missing"}` |
| NaN or infinity | `{"__type__": "float", "value": "nan"}` (`inf` or `-inf` for infinity) |
| `None` | `null` |

Tags distinguish missing values, NaN, and infinities from `null`. Unsupported
types raise an error rather than being converted to strings.

## Maintenance and scope

`HistoryStore` in `src/translation_history.py` separates persistence from the
pipeline with three operations:

- `create_run(metadata) -> run_id`
- `append(run_id, record, *, code=None) -> record_id`
- `read_run(run_id) -> list[dict]`

Stores must preserve code and records, return unique IDs, and make saved records
immediately readable. The pipeline treats IDs as references, without parsing
their format. Returned candidate records require
`type`, `record_id`, and `code_sha256`; the local reader also returns `code`.
Local JSON is the supported implementation for this issue.

Regression coverage lives in:

- `tests/test_translation_history.py`: exact code/Unicode, serialization,
  interrupted publication, atomic pointer replacement, and corruption checks.
- `tests/test_program1_integration.py`: repeated executions, review edits, CRLF
  identity, configuration snapshots, setup failures, and storage errors.
- `tests/test_translator_guards.py`: legacy outcomes without claims of review or
  validation.

### Branch scope and related work

[Issue #41 / PR #54](https://github.com/Break-Through-Tech/Consulting-1D-ai-powered-sas-migration/pull/54)
is included after integrating `main`. The optional
`run_program1_with_feedback()` wrapper and `GeminiSASTranslator.repair_program1()`
are available; see the [feedback-loop instructions](program1_integration.md#optional-validation-feedback-loop).
The examples earlier in this guide still describe separate generation and
reviewed execution.

The automatically combined code provides partial history for the feedback loop:

- Initial generation saves candidates before review through
  `generate_program1_code()`.
- Each reviewed attempt saves outcomes through `run_reviewed_program1()`, in
  that attempt's code directory. Repaired files enter history as reviewed-file
  imports, with no generation/model metadata or links to earlier loop candidates.
- Repair API requests, declined repair reviews, and wrapper-level failures are
  not recorded in a persistent loop report. The wrapper's returned `attempts`
  and final status remain in memory.
- The wrapper does not accept `history_store`, provide a persistent loop ID
  linking attempt runs. Repaired files are written as exact UTF-8 bytes and
  repair requests use the code that actually ran, but the history guarantees
  above apply only to the lower-level generation and reviewed-execution
  functions.

Complete feedback-loop persistence remains deferred. It would require recording
repairs before review, preserving candidate lineage and unchanged dependencies,
and saving review/error/final statuses under a common loop ID. The loop does not
support resumption.

History supports evaluation (#43) and stores the validation details returned by
`src/validate.py` (#39). Main's exact-reference/tolerance updates (#40) and
Programs 0/2 orchestration (#49) are included, but persistent history still covers
Program 1 only. Token/cost tracking remains outside this feature.
