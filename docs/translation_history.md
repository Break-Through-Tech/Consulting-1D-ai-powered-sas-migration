# Translation attempt history (issue #42)

The team has selected local JSON storage for the current implementation.
The repository had generated Python/CSV directories, but no durable history
convention. This implementation uses local JSON and unique artifact directories
without adding dependencies. The small storage interface keeps persistence
separate from the pipeline for future scalability; alternative backends are
outside the current scope. This implementation does not add a feedback loop,
change validation tolerances, or collect tokens/costs that the translator does
not expose.

## Contract and attempt identity

One returned Python code candidate for one step is one translation attempt.
Identical code from two generation requests still represents two candidates.
Transport/SDK/parsing exceptions without returned code are separate
`request_error` records, not candidates. Successful request retry counts are
inferable from preceding errors. A string rejected as empty is preserved along
with a `generation_error`; non-string responses have no candidate artifact.

Generation saves all returned candidates before exporting the working `.py`
file. Candidates have step metadata (including SAS code/configuration), a hash,
origin, and an initial `pending` review status. No validation result is invented
before execution. Only the model name is read from the translator, if exposed;
the client, credentials, and environment are not serialized.

Calling `run_reviewed_program1()` declares that the supplied code has been
reviewed, as required by the existing function contract. History records this as
`caller_supplied_reviewed_code`; it does not claim an identified reviewer or
independently verified approval. Generation does not execute code. Code waiting
for review remains pending. No review UI, rejection flow, or resume workflow is
introduced by this issue.

Unchanged reviewed files reuse their candidate IDs. Edited files create linked
candidates, preserving original generated code. Previously generated files with
no history are imported with origin `reviewed_file`; model/generation metadata
is left absent. Execution uses the archived in-memory code snapshot so later
working-file edits cannot change which code an outcome describes.
Working files and snapshots preserve exact UTF-8 code content, including LF or
CRLF line endings, so unchanged code retains its candidate ID.

Each execution has a unique ID and a mapping of all candidate IDs, including
the shared macro dependency. Each `execution_started` record stores the effective `execution_plan`,
including measure settings. That validated snapshot is also used at runtime;
changing a configuration file at the same path cannot alter earlier history.
Candidate metadata continues to describe the original generation context.
Append-only outcomes retain validation results, structured errors, and blocked
states, including when `on_error="raise"`.
Macro validation is `not_applicable`; group validation is `not_completed` until
a validation result exists. Re-executing unchanged code creates new outcomes,
not new translation candidates. Earlier results survive subsequent failures.
Configuration/input preflight failures retain existing exception behavior and
do not claim an execution or validation occurred.
After execution is registered, output-directory creation and stale-output
cleanup failures append an `execution_error` with its execution ID, setup stage,
operation, path, and exception details before re-raising the original exception.
This also applies in report mode, preserving existing setup exception behavior.

## Backend interface

`HistoryStore` in `src/translation_history.py` defines three operations:

- `create_run(metadata) -> run_id`
- `append(run_id, record, *, code=None) -> record_id`
- `read_run(run_id) -> list[dict]`

IDs are opaque to the pipeline. Stores must preserve records and code, return
unique IDs, and include `type`, `record_id`, and `code_sha256` for candidate
records returned by `read_run`. The local reader also includes `code` for code
artifacts. A backend must provide read-after-write visibility. Storage failures
propagate even in report mode: a failed write must not become a successful
pipeline result. A storage error while recording a failed request becomes
`HistoryStorageError`, so even a backend timeout cannot trigger another model
request.

Pass the same backend to generation and execution with `history_store=store`.
The default is `LocalJSONHistoryStore(code_dir / ".translation_history")`.
The sibling `translation_history.json` pointer contains run/candidate IDs and
hashes, keeping backend paths out of pipeline code. An existing pointer must
resolve in the chosen backend; missing/corrupt history is reported, not reset.

```python
from src.program1_integration import generate_program1_code, run_reviewed_program1
from src.translation_history import LocalJSONHistoryStore

store = LocalJSONHistoryStore("output/review_batch/.translation_history")
files = generate_program1_code(translator, code_dir="output/review_batch", history_store=store)
# Review the returned Python files before calling run_reviewed_program1.
result = run_reviewed_program1(code_dir="output/review_batch", history_store=store)
```

To read results later without API calls or execution:

```python
import json
from pathlib import Path
from src.translation_history import LocalJSONHistoryStore

code_dir = Path("output/review_batch")
pointer = json.loads((code_dir / "translation_history.json").read_text(encoding="utf-8"))
store = LocalJSONHistoryStore(code_dir / ".translation_history")
records = store.read_run(pointer["run_id"])
candidates = {r["record_id"]: r for r in records if r["type"] == "candidate"}
outcomes = [r for r in records if r["type"] == "outcome"]
```

## Local JSON layout and limitations

```text
<code_dir>/
  <step_id>.py                  working files for review
  translation_history.json    opaque run and current candidate references
  .translation_history/
    <run UUID>/
      <record UUID>/
        record.json            schema_version=1, UTC recorded_at, record payload
        code.py                present only when a code snapshot exists
```

Each record is published by renaming a uniquely staged directory after all
files are written. Readers ignore staging directories left by interruptions.
Pointers use temporary-file replacement. Published records are immutable;
readers verify code hashes and raise on corrupt records. This prevents partial
JSON publication, but does not promise survival of power loss (no fsync), shared
filesystem semantics, transactional pointer/record commits, or concurrent writers
to one code directory. Use separate code directories for concurrent jobs.
An interrupted pointer write can leave a published candidate unreferenced by
the pointer; `read_run` still exposes that record. There is no automatic cleanup,
retention, indexing, or cross-run query service. Runs can be discovered as UUID
directories beneath the chosen local root.

JSON remains standards-compliant: NumPy scalars retain numeric/boolean types;
pandas missing values use `{"__type__": "missing"}`; NaN and infinities use
`{"__type__": "float", "value": "nan"}` (or `inf`/`-inf`). These explicit tags
distinguish non-finite/missing values from ordinary `null`. Unknown object types
raise rather than being silently converted to strings. Timestamps describe
record persistence, not inferred model request start/end times.

Default local history and pointers are ignored by Git. A custom backend root
requires its own ignore rule if located inside the repository. Records preserve
validator mismatch examples and error messages verbatim; access/retention policy
must account for the data those diagnostics contain.

The legacy `src.translator.run_translator()` entry point also records candidates,
request failures, and execution/export outcomes. Its existing behavior does not
perform validation or require review, so history explicitly records
`validation_status="not_run"` and `review_status="not_recorded"`. It does not
invent a pass result or approval. Each invocation starts a unique history run,
even though its working Python/CSV output filenames are reused. Older run
artifacts remain under the history root.

## Future scalability and follow-up work

Local JSON is the team's current storage decision and does not require another
backend decision before use. If future requirements exceed the documented local
storage limits, expected volume, concurrency, retention, and access policy can
be reviewed with the coach. The storage interface preserves that flexibility;
no alternative backend is being explored or implemented in this issue.

PR #54 / issue #41 integration remains separate until its feedback-loop changes
are on main. Its future adapter should record each repaired candidate before
review and link outcomes to those IDs. This branch does not copy or implement
that loop. The existing reviewed Program 1 workflow is supported here; other
program orchestration remains tracked in #49. Evaluation consumers (#43) can
join outcomes to candidates now, while richer validator diagnostics (#39) remain
opaque payloads rather than a second validation schema.
