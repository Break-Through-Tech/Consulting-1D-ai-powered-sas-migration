"""Collect one reviewed, Docker-isolated Program 0 or 2 observation."""
import argparse
import hashlib
import math
import os
from pathlib import Path
from time import monotonic, perf_counter, sleep

import httpx
from google import genai
from google.genai import types

from src import data_io, translator
from src.measure_program1 import MeasuredTranslator
from src.orchestrate import build_step_plan
from src.pipeline_retry import RequestNotDispatched
from src.run_program import PROGRAM_OUTPUTS, ROOT, program2_inputs
from src.step_docker import StepDockerValidator
from src.step_repair import run_steps_with_repair
from src.step_runner import generate_step_code
from src.translation_history import CandidateHistory, write_json_atomic


MAX_REQUESTS = 50
DEFAULT_MIN_INTERVAL_SECONDS = 5.0


def _check_request_limit(limit):
    if type(limit) is not int or not 1 <= limit <= MAX_REQUESTS:
        raise ValueError(f"Choose a request limit from 1 through {MAX_REQUESTS}")


def _check_min_interval(seconds):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Choose a finite, positive request interval")


def _approve(label, content):
    print(label)
    # Escape terminal controls, including bidirectional text and literal CR.
    for line in content.split("\n"):
        print("".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in line))
    return input("Type YES to approve: ") == "YES"


class FeedbackDeclined(RequestNotDispatched):
    pass


class MeasuredSteps(MeasuredTranslator):
    def __init__(self, folder, limit, approve, min_interval_seconds=DEFAULT_MIN_INTERVAL_SECONDS):
        _check_request_limit(limit)
        _check_min_interval(min_interval_seconds)
        if (genai.__version__ != "3.6.1" or os.getenv("GOOGLE_GENAI_HTTP_CLIENT") != "httpx"
                or os.getenv("PYTHON_DOTENV_DISABLED") != "1"
                or any(os.getenv(name, "").lower() in {"1", "true"} for name in
                       ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE"))):
            raise ValueError("Use google-genai 3.6.1, httpx, disabled dotenv and the Gemini Developer API")
        if not os.environ.get("GEMINI_API_KEY"):
            raise ValueError("Set GEMINI_API_KEY privately in the measurement process")
        self.min_interval_seconds = min_interval_seconds
        self._last_dispatch = None
        super().__init__(http_options=types.HttpOptions(
            base_url="https://generativelanguage.googleapis.com",
            retry_options=types.HttpRetryOptions(attempts=1),
            client_args={"follow_redirects": False, "trust_env": False,
                         "event_hooks": {"request": [self._pace_request]}},
        ))
        self.folder = folder
        self.request_limit = limit
        self.approve = approve
        self.request_id = None
        self.checkpoint_path = folder.parent / "requests.json"

    def _pace_request(self, _request):
        # HTTPX calls this after the ledger reservation and just before transport.
        if self._last_dispatch is not None:
            remaining = self.min_interval_seconds - (monotonic() - self._last_dispatch)
            if remaining > 0:
                sleep(remaining)
        self._last_dispatch = monotonic()

    def _target(self, step, feedback):
        self.step_ids = [step["id"]]
        self.attempt = 1
        if feedback is None:
            self.request_id = None
            return
        history = CandidateHistory(self.folder)
        repairs = [r for r in history.store.read_run(history.run_id)
                   if r["type"] == "repair_request" and r["step_ids"] == self.step_ids]
        current = repairs[-1]
        if current["feedback"] != feedback:
            raise ValueError("Repair feedback differs from its saved request")
        if self.request_id != current["record_id"]:
            accepted = self.approve(f"Send this feedback to Gemini for {step['id']}?", feedback) is True
            history.record({"type": "feedback_review", "repair_request_id": current["record_id"],
                            "approved": accepted})
            if not accepted:
                raise FeedbackDeclined("Declined sending repair feedback")
        self.request_id = current["record_id"]
        self.attempt += len(repairs)

    def translate_macro_definition(self, step, macro_name, feedback=None):
        self._target(step, feedback)
        return super().translate_macro_definition(step, macro_name, feedback)

    def translate_step(self, step, macro_vars, macros, feedback=None):
        self._target(step, feedback)
        return super().translate_step(step, macro_vars, macros, feedback)

    def _checkpoint(self):
        self.requests[-1]["repair_request_id"] = self.request_id
        try:
            write_json_atomic(self.checkpoint_path, {"request_limit": self.request_limit,
                                                    "api_requests": self.requests})
        except OSError as error:
            raise RuntimeError("Could not checkpoint request evidence") from error

    def _generate_content(self, **request):
        try:
            return super()._generate_content(**request)
        except genai.errors.APIError as error:
            # Preserve retry classification without persisting the SDK response or headers.
            failure = RuntimeError(f"Gemini returned HTTP status {error.code}")
            failure.code = error.code
            raise failure from None
        except (httpx.TimeoutException, httpx.ConnectError):
            raise ConnectionError("Gemini transport failed") from None
        except httpx.HTTPError:
            raise RuntimeError("Gemini HTTP request failed") from None


def measure(program, run_dir, limit, *, review=_approve,
            min_interval_seconds=DEFAULT_MIN_INTERVAL_SECONDS):
    if program not in PROGRAM_OUTPUTS:
        raise ValueError("Choose Program 0 or 2")
    _check_request_limit(limit)
    _check_min_interval(min_interval_seconds)
    plan = build_step_plan(ROOT / "manifests" / f"program{program}.json")
    run_dir.mkdir(parents=True, exist_ok=False)
    folder = run_dir / "code"
    summary = {"program": program, "status": "INCOMPLETE", "run_id": None,
               "elapsed_seconds": None, "review_seconds": None, "request_limit": limit,
               "min_interval_seconds": min_interval_seconds}
    path = run_dir / "measurement.json"
    write_json_atomic(path, summary)
    review_seconds = 0.0

    def approve(label, source):
        nonlocal review_seconds
        start = perf_counter()
        try:
            return review(label, source) is True
        finally:
            review_seconds += perf_counter() - start

    start = perf_counter()
    service = None
    try:
        validator = StepDockerValidator(program, run_dir)
        datasets, variables = (({"HC.ALLDATA_2025JUL": data_io.load_input()}, {})
                               if program == 0 else program2_inputs())
        for name in PROGRAM_OUTPUTS[program]:
            data_io.load_sas_output(name)  # Fail before generation on missing/invalid references.
        service = MeasuredSteps(folder, limit, approve, min_interval_seconds)
        summary["configuration"] = {
            "model_name": service.model_name, "prompt_version": hashlib.sha256(
                Path(translator.__file__).read_bytes()).hexdigest(), "repair_loop": True,
            "column_context": False, "fastclus_helper": True,
        }
        write_json_atomic(path, summary)
        files = generate_step_code(service, plan, folder, program=program,
                                   check_request_budget=service.check_request_budget)
        history = CandidateHistory(folder)
        summary["run_id"] = history.run_id
        approved = {}
        for step, file in files.items():
            source = file.read_bytes().decode("utf-8")
            if hashlib.sha256(source.encode()).hexdigest() != history.pointer["candidates"][step]["code_sha256"]:
                raise ValueError("Candidate file differs from recorded generation")
            accepted = approve(f"Run {step} in Docker?", source)
            history.record({"type": "review", "candidate_id": history.pointer["candidates"][step]["candidate_id"],
                            "approved": accepted})
            if not accepted:
                raise FeedbackDeclined()
            approved[step] = source
        result = run_steps_with_repair(
            plan, folder, reviewed=True, program=program, translator_factory=lambda: service,
            review_repair=lambda step, code: approve(f"Run replacement for {step['id']} in Docker?", code),
            initial_macro_vars=variables, initial_datasets=datasets, approved_snapshot=approved,
            execute_snapshot=validator, check_request_budget=service.check_request_budget,
        )
        summary["status"] = result.get("repair_status", result["status"])
    except FeedbackDeclined:
        summary["status"] = "REVIEW_REQUIRED"
    except Exception as error:
        summary.update(status="ERROR", error_type=type(error).__name__)
        raise
    finally:
        summary.update(elapsed_seconds=perf_counter() - start - review_seconds, review_seconds=review_seconds)
        if (folder / "translation_history.json").is_file():
            summary["run_id"] = CandidateHistory(folder).run_id
        write_json_atomic(path, summary)
        if service is not None:
            service.client.close()
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", choices=(0, 2), type=int)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--request-limit", required=True, type=int)
    parser.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL_SECONDS,
                        metavar="SECONDS", help="minimum seconds between POST starts (default: %(default)g)")
    args = parser.parse_args(argv)
    result = measure(args.program, args.run_dir, args.request_limit,
                     min_interval_seconds=args.min_interval)
    print(result["status"], args.run_dir)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
