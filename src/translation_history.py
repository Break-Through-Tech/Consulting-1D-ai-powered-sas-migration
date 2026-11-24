"""Backend-neutral attempt history with the team's local JSON storage.

Records are append-only. Each candidate is code, not an API request or an
execution. Later review/outcome records refer to that candidate's record ID.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import numpy as np
import pandas as pd


class HistoryStore(Protocol):
    def create_run(self, metadata: dict) -> str: ...

    def append(self, run_id: str, record: dict, *, code: str | None = None) -> str: ...

    def read_run(self, run_id: str) -> list[dict]: ...


class HistoryStorageError(RuntimeError):
    """A history write failed inside a retryable translator operation."""


class CandidateHistory:
    """Correlate generation and reviewed execution without backend paths.

    The code-directory pointer contains opaque IDs and hashes only. A custom
    store must be supplied again when executing code generated with that store.
    """

    def __init__(self, code_dir, store=None, *, metadata=None, new=False):
        self.pointer_path = Path(code_dir) / "translation_history.json"
        self.store = store if store is not None else LocalJSONHistoryStore(
            Path(code_dir) / ".translation_history"
        )
        if not new and self.pointer_path.exists():
            self.pointer = json.loads(self.pointer_path.read_text(encoding="utf-8"))
            if self.pointer.get("schema_version") != 1:
                raise ValueError("Unsupported translation history pointer version.")
            # Ensure the selected backend contains the referenced candidates.
            self.known = {
                record["record_id"]: record
                for record in self.store.read_run(self.pointer["run_id"])
                if record["type"] == "candidate"
            }
            pending_id = self.pointer.get("pending_publication")
            if pending_id is not None:
                pending = self.known[pending_id]
                path = Path(code_dir) / f"{pending['step_id']}.py"
                if code_hash(path.read_bytes().decode("utf-8")) != pending["code_sha256"]:
                    raise ValueError("Interrupted repair publication: working file does not match the approved candidate.")
                self.select_candidate(pending_id)
        else:
            self.pointer = {
                "schema_version": 1,
                "run_id": self.store.create_run(metadata or {}),
                "candidates": {},
            }
            self.known = {}
            write_json_atomic(self.pointer_path, self.pointer)

    @property
    def run_id(self):
        return self.pointer["run_id"]

    def record(self, record):
        return self.store.append(self.run_id, record)

    def candidate(self, step, code, *, origin, reuse=False, previous_candidate_id=None,
                  repair_request_id=None, select=True):
        previous = self.pointer["candidates"].get(step["id"])
        if previous:
            archived = self.known.get(previous["candidate_id"])
            if archived is None or archived["code_sha256"] != previous["code_sha256"]:
                raise ValueError("Candidate pointer does not match stored history.")
            if reuse and previous["code_sha256"] == code_hash(code):
                return previous["candidate_id"]
        record = {
            "type": "candidate", "step_id": step["id"], "step": step,
            "origin": origin,
            "review_status": "caller_supplied_reviewed_code" if origin == "reviewed_file" else "pending",
        }
        if previous and previous_candidate_id is None:
            previous_candidate_id = previous["candidate_id"]
        if previous_candidate_id:
            record["previous_candidate_id"] = previous_candidate_id
        if repair_request_id:
            record["repair_request_id"] = repair_request_id
        candidate_id = self.store.append(self.run_id, record, code=code)
        self.known[candidate_id] = {**record, "code_sha256": code_hash(code)}
        if select:
            self.select_candidate(candidate_id)
        return candidate_id

    def begin_publication(self, candidate_id):
        """Persist the approved identity before changing the working file."""
        self.pointer["pending_publication"] = candidate_id
        write_json_atomic(self.pointer_path, self.pointer)

    def select_candidate(self, candidate_id):
        """Point the working file at a saved candidate after publishing its code."""
        archived = self.known[candidate_id]
        self.pointer["candidates"][archived["step_id"]] = {
            "candidate_id": candidate_id, "code_sha256": archived["code_sha256"],
        }
        self.pointer.pop("pending_publication", None)
        write_json_atomic(self.pointer_path, self.pointer)


def code_hash(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def error_details(error):
    details = {"type": type(error).__name__, "message": str(error)}
    if hasattr(error, "attempts"):
        details["attempts"] = error.attempts
        details["cause_type"] = type(error.original_error).__name__
    return details


def _json_value(value):
    """Keep numeric types numeric and encode non-JSON values explicitly."""
    if value is pd.NA or value is pd.NaT:
        return {"__type__": "missing"}
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return {"__type__": "float", "value": str(value)}
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def write_json_atomic(path, value):
    """Replace a JSON file only after the complete payload has been written."""
    path = Path(path)
    payload = json.dumps(_json_value(value), ensure_ascii=False, allow_nan=False, indent=2)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class LocalJSONHistoryStore:
    """Single-machine storage; not a shared-service or durability guarantee.

    Published record directories are immutable. Staging directories are ignored
    by readers, so interrupted writes cannot appear to be completed records.
    """

    def __init__(self, root):
        self.root = Path(root)

    def _run_path(self, run_id):
        # IDs never become arbitrary filesystem paths, including on Windows.
        if not isinstance(run_id, str) or UUID(run_id).hex != run_id:
            raise ValueError("History run IDs must be canonical UUID hex strings.")
        return self.root / run_id

    def create_run(self, metadata):
        run_id = uuid4().hex
        directory = self._run_path(run_id)
        directory.mkdir(parents=True, exist_ok=False)
        self.append(run_id, {"type": "run", "metadata": metadata})
        return run_id

    def append(self, run_id, record, *, code=None):
        directory = self._run_path(run_id)
        if not directory.is_dir():
            raise FileNotFoundError(f"Unknown history run: {run_id}")
        record_id = uuid4().hex
        payload = {
            **record,
            # One writer per run. Count published records, including legacy ones.
            "sequence": sum(1 for child in directory.iterdir()
                            if not child.name.startswith(".") and child.is_dir()),
            "schema_version": 1,
            "run_id": run_id,
            "record_id": record_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        if code is not None:
            payload["code_sha256"] = code_hash(code)
        # Serialize before creating any artifacts to reject unsupported values.
        serialized = json.dumps(_json_value(payload), ensure_ascii=False, allow_nan=False, indent=2)
        staging = directory / f".{record_id}.tmp"
        staging.mkdir()
        if code is not None:
            with (staging / "code.py").open("w", encoding="utf-8", newline="") as file:
                file.write(code)
        (staging / "record.json").write_text(serialized, encoding="utf-8")
        staging.rename(directory / record_id)
        return record_id

    def read_run(self, run_id):
        directory = self._run_path(run_id)
        if not directory.is_dir():
            raise FileNotFoundError(f"Unknown history run: {run_id}")
        records = []
        for child in directory.iterdir():
            if child.name.startswith(".") or not child.is_dir():
                continue
            record = json.loads((child / "record.json").read_text(encoding="utf-8"))
            if "code_sha256" in record:
                code = (child / "code.py").read_bytes().decode("utf-8")
                if code_hash(code) != record["code_sha256"]:
                    raise ValueError(f"History code hash mismatch: {child}")
                record["code"] = code
            records.append(record)
        # Legacy records keep their original timestamp order before sequenced appends.
        return sorted(records, key=lambda item: (
            item.get("sequence", -1), item["recorded_at"], item["record_id"],
        ))


def read_program1_attempts(folder):
    """Read a feedback loop in attempt order, verifying its run and candidate links."""
    folder = Path(folder)
    numbers = sorted(int(p.name[8:]) for p in folder.glob("attempt_*")
                     if p.is_dir() and p.name[8:].isdigit())
    if not numbers or numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"Missing Program 1 attempt in {folder}")
    attempts = []
    loop_id = previous_id = None
    previous_candidates = {}
    for number in numbers:
        code_dir = folder / f"attempt_{number}" / "code"
        pointer = code_dir / "translation_history.json"
        if not pointer.is_file():
            raise ValueError(f"Incomplete attempt: missing history pointer in {code_dir}")
        run_id = json.loads(pointer.read_text(encoding="utf-8"))["run_id"]
        records = LocalJSONHistoryStore(code_dir / ".translation_history").read_run(run_id)
        header = next(r for r in records if r["type"] == "run")
        metadata = header["metadata"]
        if (header["run_id"] != run_id or metadata.get("program") != 1
                or metadata.get("attempt") != number or metadata.get("repair_history") is not True):
            raise ValueError(f"Invalid Program 1 attempt metadata in {code_dir}")
        if number == 1:
            loop_id = run_id
        elif metadata.get("loop_id") != loop_id or metadata.get("previous_run_id") != previous_id:
            raise ValueError(f"Unlinked Program 1 history in {code_dir}")
        candidates = {}
        for record in records:
            if record["type"] == "candidate":
                parent = record.get("previous_candidate_id")
                if parent or record["origin"] in {"repair", "carried_forward"}:
                    if candidates.get(parent, previous_candidates.get(parent)) != record["step_id"]:
                        raise ValueError(f"Unknown candidate predecessor in {code_dir}")
                candidates[record["record_id"]] = record["step_id"]
        attempts.append(records)
        previous_id, previous_candidates = run_id, candidates
    return attempts
