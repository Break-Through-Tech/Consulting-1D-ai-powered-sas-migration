"""Bounded, reviewed repairs for runtime failures in the Program 0 and 2 reports."""

from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp

from src.step_runner import (
    SavedStepCode, StepExecutionError, _report_snapshot, _signature, macro_name,
    request_step_candidate,
)
from src.translation_history import CandidateHistory, error_details


IMPORT_DIR = Path(__file__).resolve().parents[1] / "output" / "step_repairs"


def _publish_reviewed_code(path, code):
    # A failed write must leave the previous working file intact.
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        pending = Path(temporary.name)
    try:
        pending.write_bytes(code.encode("utf-8"))
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def _review_replacement(step, code, candidate_id, history, saved, review_repair):
    try:
        approved = review_repair(step, code) is True
    except (Exception, KeyboardInterrupt) as error:
        history.record({"type": "review_error", "candidate_id": candidate_id, "error": error_details(error)})
        raise
    history.record({"type": "review", "candidate_id": candidate_id, "approved": approved})
    if approved:
        history.begin_publication(candidate_id)
        try:
            _publish_reviewed_code(saved.code_dir / f"{step['id']}.py", code)
        except OSError as error:
            history.record({"type": "artifact_error", "candidate_id": candidate_id, "error": error_details(error)})
            raise
        history.select_candidate(candidate_id)
        saved.snapshot[step["id"]] = code
    return approved


def run_steps_with_repair(plan, code_dir, *, reviewed, translator_factory, review_repair,
                          initial_macro_vars=None, initial_datasets=None, program=None):
    """Replay a reviewed snapshot, asking only for steps that actually failed.

    Approved replacements update the generated working files. Every returned
    candidate, including declined and invalid code, remains in immutable history.
    """
    if reviewed is not True or not callable(review_repair):
        raise StepExecutionError("Review the initial files and supply a repair review callback.")
    code_dir = Path(code_dir)
    saved = SavedStepCode(code_dir)
    saved.freeze(plan)
    if (code_dir / "translation_history.json").is_file():
        history = CandidateHistory(code_dir)
    else:
        # Preserve older working files and checked-in fixtures. Past generation is unknown.
        IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        folder = Path(mkdtemp(prefix="run-", dir=IMPORT_DIR))
        for step_id, code in saved.snapshot.items():
            (folder / f"{step_id}.py").write_bytes(code.encode("utf-8"))
        saved.code_dir = folder
        metadata = {"repair_history": True, "source": "reviewed_files"}
        if program is not None:
            metadata["program"] = program
        history = CandidateHistory(folder, metadata=metadata)
    records = history.store.read_run(history.run_id)
    metadata = next(record["metadata"] for record in records if record["type"] == "run")
    if (metadata.get("repair_history") is not True or
            program is not None and metadata.get("program", program) != program):
        raise ValueError("Code directory does not contain this program's complete generation history.")
    fixes = Counter(step_id for r in records if r["type"] == "repair_request" for step_id in r["step_ids"])
    translator = None
    while True:
        report = _report_snapshot(plan, saved, history, initial_macro_vars, initial_datasets)
        report["code_dir"] = str(saved.code_dir)
        failures = {entry["id"]: entry["error"] for entry in report["steps"] if entry["status"] == "failed"}
        if not failures:
            return report
        changed = False
        signatures = {}
        for step in plan:
            step_id = step["id"]
            if step_id not in saved.snapshot:
                continue
            if step_id in failures:
                feedback = f"{failures[step_id]}\nPrevious code:\n{saved.snapshot[step_id]}"
                previous_id = history.pointer["candidates"][step_id]["candidate_id"]
                while fixes[step_id] < 2:  # Three attempts, including initial generation.
                    if translator is None:
                        translator = translator_factory()
                    fixes[step_id] += 1
                    code, problem, candidate_id = request_step_candidate(
                        translator, step, signatures, history, feedback=feedback,
                        execution_id=report["execution_id"], previous_candidate_id=previous_id,
                    )
                    if problem:
                        previous_id = candidate_id or previous_id
                        feedback = f"Your previous answer was rejected: {problem}\nPrevious code:\n{code}"
                        continue
                    if not _review_replacement(step, code, candidate_id, history, saved, review_repair):
                        report["repair_status"] = "REVIEW_REQUIRED"
                        return report
                    changed = True
                    break
            if step["kind"] == "macro":
                name = macro_name(step)
                try:
                    signature = _signature(name, saved.snapshot[step_id])
                except SyntaxError:
                    signature = None  # A manually edited macro may be the failing step.
                if signature:
                    signatures[name] = signature
        if not changed:
            return report
