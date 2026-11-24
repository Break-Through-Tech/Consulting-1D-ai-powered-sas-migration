"""Measure one Program 1 feedback run. Each candidate requires interactive review."""

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from google.genai import types

from src import translator
from src.program1_docker import DockerValidator
from src.program1_integration import run_program1_with_feedback
from src.translation_history import LocalJSONHistoryStore, write_json_atomic


class MeasuredTranslator(translator.GeminiSASTranslator):
    def __init__(self):
        super().__init__(http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1),
            client_args={"follow_redirects": False},
        ))
        self.attempt = 1
        self.requests = []
        self.repairs = {}
        self.step_ids = []
        self.checkpoint_path = None

    def _checkpoint(self):
        if self.checkpoint_path is not None:
            try:
                write_json_atomic(self.checkpoint_path, {
                    "status": "INCOMPLETE", "histories": [], "elapsed_seconds": None,
                    "model_name": self.model_name, "api_requests": self.requests,
                    "repair_requests": self.repairs,
                })
            except OSError as error:
                # Storage failures must never trigger another paid SDK attempt.
                raise RuntimeError("Could not checkpoint Program 1 request evidence") from error

    def translate_macro(self, step_info):
        self.step_ids = [step_info["id"]]
        return super().translate_macro(step_info)

    def translate_group_step(self, step_info, measures):
        self.step_ids = [step_info["id"]]
        return super().translate_group_step(step_info, measures)

    def repair_program1(self, steps, previous_code, feedback):
        # The feedback loop retries transient errors within the same candidate round.
        self.repairs[self.attempt] = [step["id"] for step in steps]
        self.step_ids = self.repairs[self.attempt]
        self._checkpoint()
        return super().repair_program1(steps, previous_code, feedback)

    def _program1_generate_content(self, **request):
        # Six initial translations and two batched repairs, each with up to three tries.
        if len(self.requests) >= 24:
            raise ValueError("Program 1 measurement stopped at the 24-request limit.")
        request["config"].automatic_continuation = False
        call = {"attempt": self.attempt, "step_ids": self.step_ids, "status": "reserved",
                "elapsed_seconds": None, "usage": None}
        self.requests.append(call)
        self._checkpoint()
        start = perf_counter()
        try:
            response = super()._program1_generate_content(**request)
        except Exception:
            call["elapsed_seconds"] = perf_counter() - start
            call["status"] = "failed"
            self._checkpoint()
            raise
        call["elapsed_seconds"] = perf_counter() - start
        if response.usage_metadata is not None:
            call["usage"] = response.usage_metadata.model_dump(mode="json", exclude_none=True)
        call["status"] = "completed"
        self._checkpoint()
        candidates = response.candidates or []
        if not candidates:
            raise ValueError("Gemini returned no translation candidate.")
        for candidate in candidates:
            if candidate.continuation_token or candidate.finish_reason == types.FinishReason.CONTINUATION:
                raise ValueError("Gemini requested continuation. The measured run stopped before using partial code.")
            if candidate.finish_reason != types.FinishReason.STOP:
                raise ValueError(f"Incomplete Gemini translation (finish reason: {candidate.finish_reason}).")
        return response


def measure(service, run_dir, review_code, *, max_attempts=3):
    review_seconds = 0.0

    def review(files):
        nonlocal review_seconds
        start = perf_counter()
        try:
            return review_code(files)
        finally:
            review_seconds += perf_counter() - start
            service.attempt += 1

    validator = DockerValidator()
    service.checkpoint_path = run_dir / "measurement.json"
    start = perf_counter()
    try:
        report = run_program1_with_feedback(service, review, run_dir=run_dir, max_attempts=max_attempts,
                                            validator=validator)
    finally:
        service.checkpoint_path = None
    elapsed = perf_counter() - start
    completed = {item["attempt"]: item["status"] for item in report["attempts"]}
    histories = []
    # Each attempt has its own store, including a first generation that stopped early.
    for number in range(1, report["attempt"] + 1):
        code_dir = run_dir / f"attempt_{number}" / "code"
        pointer = code_dir / "translation_history.json"
        if not pointer.exists() and number not in completed:
            continue
        run_id = json.loads(pointer.read_text(encoding="utf-8"))["run_id"]
        records = LocalJSONHistoryStore(code_dir / ".translation_history").read_run(run_id)
        executions = [r["execution_id"] for r in records if r["type"] == "execution_started"]
        if len(executions) > 1:
            raise ValueError(f"Ambiguous executions in {code_dir}")
        histories.append({
            "attempt": number, "run_id": run_id,
            "execution_id": next(iter(executions), None), "status": completed.get(number),
        })
    evidence = {
        "histories": histories, "status": report["status"], "error": report["error"],
        "repair_requests": service.repairs, "api_requests": service.requests,
        "elapsed_seconds": elapsed - review_seconds, "review_seconds": review_seconds,
        "configuration": {
            "model_name": service.model_name,
            "prompt_version": hashlib.sha256(Path(translator.__file__).read_bytes()).hexdigest(),
            "repair_loop": max_attempts > 1,
        },
        "max_attempts": max_attempts,
    }
    write_json_atomic(run_dir / "measurement.json", evidence)
    return evidence


def _review(files):
    for step, path in files.items():
        print(f"{step}  {path.resolve()}")
    return input("Read every file. Type RUN to execute this attempt: ").strip() == "RUN"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args(argv)
    evidence = measure(MeasuredTranslator(), args.run_dir, _review, max_attempts=args.max_attempts)
    print(evidence["status"], args.run_dir / "measurement.json")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
