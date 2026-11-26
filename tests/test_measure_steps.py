"""Test the Gemini request flow using an in-memory HTTP transport without real API calls or credentials."""
import json
import os
from collections import Counter
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from google import genai

from src import data_io, measure_steps, migration_results
from src.program1_docker import DockerValidator
from src.run_program import PROGRAM_OUTPUTS, ROOT
from src.step_docker import StepDockerValidator
from src.translation_history import CandidateHistory
from src.verify_step_measurement import verify


class StepMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory()))
        self.run_dir = self.folder / "run"
        self.posts = Counter()
        self.clock = 0.0
        self.failures = 0
        self.transport_errors = 0
        self.worker_interrupt = False
        self.first_step = {0: "data_work_all_data_2025jul", 2: "call_report"}
        self.review = lambda *_: True
        settings = {k: os.environ[k] for k in ("SystemRoot", "TEMP", "TMP") if k in os.environ}
        settings.update(GEMINI_API_KEY="offline-placeholder", GOOGLE_GENAI_HTTP_CLIENT="httpx",
                        PYTHON_DOTENV_DISABLED="1")
        self.enterContext(patch("os.environ", settings))
        ctor = genai.Client

        def client(*, api_key, http_options):
            self.assertEqual(api_key, "offline-placeholder")
            options = http_options.client_args
            self.assertFalse(options["trust_env"])
            options["transport"] = httpx.MockTransport(self.response)
            return ctor(api_key=api_key, vertexai=False, http_options=http_options)

        self.enterContext(patch("src.translator.genai.Client", side_effect=client))
        self.enterContext(patch("src.measure_steps.monotonic", side_effect=lambda: self.clock))
        self.enterContext(patch("src.measure_steps.sleep", side_effect=self.advance_clock))
        self.enterContext(patch("src.pipeline_retry.time.sleep", side_effect=self.advance_clock))

    def advance_clock(self, seconds):
        self.clock += seconds

    def response(self, request):
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.host, "generativelanguage.googleapis.com")
        self.assertTrue(request.url.path.endswith("/models/gemini-3.5-flash-lite:generateContent"))
        prompt = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        step = re.search(r"Step ID: (\w+)", prompt)[1]
        self.posts[step] += 1
        if self.transport_errors:
            self.transport_errors -= 1
            return httpx.Response(503, json={"error": {"message": "private response detail"}})
        path = ROOT / "tests" / "fixtures" / f"program{self.program}_generated" / f"{step}.py"
        code = path.read_text(encoding="utf-8")
        if step == self.first_step[self.program] and self.posts[step] <= self.failures:
            code = "raise ValueError('offline runtime failure')"
        if self.worker_interrupt and step == self.first_step[self.program]:
            code = "raise KeyboardInterrupt"
        if self.probe and step == self.first_step[self.program]:
            code = ("import os, socket\n"
                    "assert os.getuid() == 65534 and 'GEMINI_API_KEY' not in os.environ\n"
                    "assert set(os.listdir('/sys/class/net')) == {'lo'}\n"
                    "assert not any(os.path.exists(p) for p in "
                    "('/inputs/.git', '/inputs/.env', '/inputs/references', '/var/run/docker.sock'))\n"
                    "assert socket.socket().connect_ex(('192.0.2.1', 443)) != 0\n" + code)
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": json.dumps({"python_code": code})}]}}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 10}})

    def fake_worker(self, root, *_):
        job = json.loads((root / "job.json").read_text())
        steps = []
        for step in job["plan"]:
            status, error = "skipped", None
            if step["action"] == "translate":
                code = (root / "code" / f"{step['id']}.py").read_text(encoding="utf-8")
                status = "failed" if code.startswith("raise ValueError") else "ok"
                error = "offline runtime failure" if status == "failed" else None
            steps.append({"id": step["id"], "kind": step["kind"], "status": status,
                          "error": error, "blocked_by": []})
        self.assertFalse((root / "references").exists())
        tables = {name: data_io.load_sas_output(name).to_csv(index=False) for name in job["outputs"]}
        return json.dumps({"steps": steps, "tables": tables}).encode()

    def collect(self, program, *, real=False, limit=50, **pacing):
        self.program, self.probe = program, real
        if not real:
            self.enterContext(patch.object(DockerValidator, "__init__", return_value=None))
            self.enterContext(patch.object(StepDockerValidator, "_invoke", side_effect=self.fake_worker))
        # Any host execution of a translated step fails the test, even if caught by the runner.
        with patch("src.step_runner.StepRunner._exec", side_effect=AssertionError("host execution")) as host:
            try:
                return measure_steps.measure(program, self.run_dir, limit, review=self.review, **pacing)
            finally:
                host.assert_not_called()

    def exported(self):
        run = migration_results.measured_step_result(self.run_dir)
        history = CandidateHistory(self.run_dir / "code")
        alone = migration_results.history_result(self.program, history.store.read_run(history.run_id))
        self.assertEqual(run["steps"], alone["steps"])
        return run

    def test_program0_first_try_and_preserved_demo(self):
        self.assertEqual(self.collect(0)["status"], "PASS")
        run = self.exported()
        self.assertEqual((run["first_try_passed"], run["first_try_measured"]), (33, 33))
        self.assertTrue(all(s["fix_attempts"] == 0 for s in run["steps"]))
        self.assertIsNone(run["cost_usd"])
        self.assertEqual(len(run["outputs"]), 3)
        verify(0, self.run_dir)
        demo = ROOT / "demo/results.json"
        dest = self.folder / "candidate.json"
        args = ["--append-to", str(demo), "--step-run", str(self.run_dir), "--output", str(dest)]
        migration_results.main(args)
        saved = json.loads(demo.read_text())
        self.assertEqual(json.loads(dest.read_text()), {**saved, "runs": [*saved["runs"], run]})
        with self.assertRaises(FileExistsError):
            migration_results.main(args)
        with self.assertRaises(FileExistsError):
            self.collect(0)
        csv, = self.run_dir.glob("executions/*/LESS100_MEASURE.csv")
        csv.write_text("wrong_column\n0\n")
        with self.assertRaisesRegex(ValueError, "LESS100_MEASURE"):
            verify(0, self.run_dir)

    def test_program2_repair_and_request_attribution(self):
        self.failures = 1
        self.transport_errors = 1
        prompts = []
        normal_response = self.response

        def capture(request):
            prompts.append(json.loads(request.content)["contents"][0]["parts"][0]["text"])
            return normal_response(request)

        self.response = capture
        self.assertEqual(self.collect(2)["status"], "PASS")
        run = self.exported()
        self.assertEqual((run["first_try_passed"], run["first_try_measured"]), (30, 31))
        fixed, = [s for s in run["steps"] if s["fix_attempts"]]
        self.assertEqual((fixed["id"], fixed["fix_attempts"], fixed["first_try_success"]), ("call_report", 1, False))
        calls = json.loads((self.run_dir / "requests.json").read_text())["api_requests"]
        self.assertEqual(len(calls), sum(self.posts.values()))
        self.assertEqual((calls[0]["status"], calls[0]["usage"]), ("failed", None))
        self.assertEqual(calls[-1]["usage"]["total_token_count"], 10)
        h = CandidateHistory(self.run_dir / "code")
        records = h.store.read_run(h.run_id)
        request, = [r for r in records if r["type"] == "repair_request"]
        self.assertEqual(calls[-1]["repair_request_id"], request["record_id"])
        macro, = [r for r in records if r["type"] == "candidate" and r["step_id"] == "report_macro"]
        self.assertEqual(request["macro_candidates"], {"report_macro": macro["record_id"]})
        self.assertIn(macro["record_id"], prompts[-1])
        self.assertIn(macro["code"], prompts[-1])
        self.assertIn(request["feedback"], prompts[-1])
        self.assertTrue(all("Shared macro report_macro" not in prompt for prompt in prompts[:-1]))
        self.assertNotIn("def kmeans(", prompts[-1])
        verify(2, self.run_dir)
        path = self.run_dir / "requests.json"
        journal = json.loads(path.read_bytes())
        del journal["api_requests"][0]
        path.write_text(json.dumps(journal), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Request slots differ"):
            verify(2, self.run_dir)

    def test_exhausted_candidates(self):
        self.failures = 3
        self.assertEqual(self.collect(2)["status"], "FAIL")
        run = self.exported()
        self.assertEqual(self.posts["call_report"], 3)
        self.assertEqual(next(s for s in run["steps"] if s["id"] == "call_report")["fix_attempts"], 2)

    def test_output_mismatch_after_successful_execution_does_not_request_repair(self):
        worker = self.fake_worker

        def wrong_output(root, *args):
            payload = json.loads(worker(root, *args))
            payload["tables"]["NATIONAL_AVERAGE_2025JUL"] = "wrong_column\n0\n"
            return json.dumps(payload).encode()

        self.fake_worker = wrong_output
        self.assertEqual(self.collect(2)["status"], "FAIL")
        run = self.exported()
        self.assertEqual(run["outputs"]["NATIONAL_AVERAGE_2025JUL"]["status"], "FAIL")
        self.assertEqual(run["first_try_passed"], 31)
        self.assertTrue(all(s["fix_attempts"] == 0 for s in run["steps"]))
        self.assertEqual(sum(self.posts.values()), 31)

    def test_declined_initial_review(self):
        self.review = lambda *_: False
        self.assertEqual(self.collect(2)["status"], "REVIEW_REQUIRED")
        self.assertEqual(self.exported()["first_try_measured"], 0)
        self.assertFalse((self.run_dir / "executions").exists())

    def test_changed_reviewed_file(self):
        def edit(label, code):
            if "call_report" in label:
                (self.run_dir / "code/call_report.py").write_text("pass")
            return True
        self.review = edit
        with self.assertRaisesRegex(ValueError, "changed after review"):
            self.collect(2)
        self.assertFalse((self.run_dir / "executions").exists())

    def test_interrupted_worker(self):
        worker = self.fake_worker
        self.failures = 1

        def interrupt_repair(root, *args):
            if self.posts["call_report"] == 2:
                raise KeyboardInterrupt
            return worker(root, *args)

        self.fake_worker = interrupt_repair
        with self.assertRaises(KeyboardInterrupt):
            self.collect(2)
        run = self.exported()
        self.assertEqual((run["status"], run["first_try_measured"], run["outputs"]), ("INCOMPLETE", 31, {}))
        repaired, = [s for s in run["steps"] if s["fix_attempts"]]
        self.assertEqual((repaired["id"], repaired["first_try_success"], repaired["fix_attempts"]),
                         ("call_report", False, 1))
        self.assertEqual(sum(self.posts.values()), 32)
        self.assertEqual(len(list(self.run_dir.glob("executions/*/validation.json"))), 1)

    def test_declined_feedback_or_replacement(self):
        for prefix, calls in (("Send this feedback", 1), ("Run replacement", 2)):
            with self.subTest(prefix=prefix):
                self.run_dir = self.folder / str(calls)
                self.posts.clear()
                self.failures = 1
                self.review = lambda label, _: not label.startswith(prefix)
                self.assertEqual(self.collect(2)["status"], "REVIEW_REQUIRED")
                self.assertEqual(self.posts["call_report"], calls)
                h = CandidateHistory(self.run_dir / "code")
                rows = h.store.read_run(h.run_id)
                self.assertEqual(sum(r["type"] == "execution_started" for r in rows), 1)
                feedback, = [r for r in rows if r["type"] == "feedback_review"]
                self.assertEqual(feedback["approved"], calls == 2)
                self.assertEqual(self.exported()["outputs"].keys(), set(PROGRAM_OUTPUTS[2]))
                self.assertEqual(sum(s["fix_attempts"] for s in self.exported()["steps"]), calls - 1)

    def test_interrupt_during_post_preserves_the_reservation(self):
        def interrupt(request):
            calls = json.loads((self.run_dir / "requests.json").read_text())["api_requests"]
            self.assertEqual((len(calls), calls[0]["status"], calls[0]["usage"]), (1, "reserved", None))
            raise KeyboardInterrupt

        self.response = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.collect(0)
        call, = json.loads((self.run_dir / "requests.json").read_text())["api_requests"]
        self.assertIsNone(call["elapsed_seconds"])
        self.assertEqual(self.exported()["status"], "INCOMPLETE")
        with self.assertRaisesRegex(ValueError, "No terminal validation"):
            verify(0, self.run_dir)

    def test_continuation_and_redirect_stop_before_review(self):
        for status in (200, 307):
            with self.subTest(status=status):
                self.run_dir = self.folder / str(status)
                calls = []
                def partial(request):
                    calls.append(request.method)
                    self.assertEqual(calls, ["POST"])
                    return httpx.Response(status, headers={"Location": "/unapproved"}, json={"candidates": [{
                        "finishReason": "CONTINUATION", "continuationToken": "YWJj",
                        "content": {"parts": [{"text": '{"python_code":"pass"}'}]}}]})
                self.response = partial
                self.review = lambda *_: self.fail("partial response reached review")
                with self.assertRaises((ValueError, RuntimeError)):
                    self.collect(0)
                self.assertEqual(calls, ["POST"])
                self.assertFalse((self.run_dir / "executions").exists())

    def test_absent_input_or_docker(self):
        with patch("src.measure_steps.StepDockerValidator", side_effect=RuntimeError("no Docker")):
            with self.assertRaisesRegex(RuntimeError, "no Docker"):
                self.collect(0)
        self.run_dir = self.folder / "missing"
        with patch("src.data_io.load_input", side_effect=FileNotFoundError("missing input")):
            with self.assertRaises(FileNotFoundError):
                self.collect(0)
        self.assertFalse(self.posts)

    def test_request_cap_counts_transport_attempts(self):
        self.transport_errors = 5
        with self.assertRaisesRegex(ValueError, "2-request limit"):
            self.collect(0, limit=2)
        self.assertEqual(sum(self.posts.values()), 2)

    def test_dispatch_pacing_covers_generation_and_both_kinds_of_repair(self):
        normal_response = self.response
        self.transport_errors = 1
        dispatches = []

        def respond(request):
            dispatches.append(self.clock)
            response = normal_response(request)
            # Short responses and the existing 2s backoff consume part of the interval.
            self.advance_clock(7 if len(dispatches) == 2 else 0.5)
            prompt = json.loads(request.content)["contents"][0]["parts"][0]["text"]
            if "Step ID: call_report\n" in prompt:
                candidate_number = self.posts["call_report"]
                if candidate_number in (1, 2):
                    payload = response.json()
                    code = "if" if candidate_number == 1 else "raise ValueError('offline runtime failure')"
                    payload["candidates"][0]["content"]["parts"][0]["text"] = json.dumps({"python_code": code})
                    return httpx.Response(200, json=payload)
            return response

        self.response = respond
        result = self.collect(2)
        self.assertEqual((result["status"], result["min_interval_seconds"]), ("PASS", 5.0))
        self.assertEqual(dispatches[:4], [0, 5, 12, 17])
        gaps = [b - a for a, b in zip(dispatches[2:], dispatches[3:])]
        self.assertEqual(gaps, [5] * (len(dispatches) - 3))
        self.assertEqual(len(dispatches), 34)  # 31 steps, one transport retry and two fixes
        fixed, = [s for s in self.exported()["steps"] if s["fix_attempts"]]
        self.assertEqual((fixed["id"], fixed["fix_attempts"], fixed["first_try_success"]), ("call_report", 2, False))
        verify(2, self.run_dir)

    def test_custom_interval_paces_retry_and_ignores_request_beyond_budget(self):
        dispatches = []

        def unavailable(request):
            dispatches.append(self.clock)
            return httpx.Response(429, json={"error": {"message": "offline throttle"}})

        self.response = unavailable
        with self.assertRaisesRegex(ValueError, "2-request limit"):
            self.collect(0, limit=2, min_interval_seconds=6.5)
        self.assertEqual(dispatches, [0, 6.5])
        self.assertEqual(self.clock, 10.5)  # Final retry backoff elapsed, with no pacing for the rejected request.
        self.assertEqual(json.loads((self.run_dir / "measurement.json").read_bytes())["min_interval_seconds"], 6.5)

    def test_both_programs_reject_more_than_50_before_setup(self):
        for program in (0, 2):
            with self.subTest(program=program), self.assertRaisesRegex(ValueError, "1 through 50"):
                self.collect(program, limit=51)
            self.assertFalse(self.run_dir.exists())
        self.assertFalse(self.posts)

    def test_each_program_stops_at_50_posts_including_retries(self):
        normal_response = self.response
        for program in (0, 2):
            with self.subTest(program=program):
                self.run_dir = self.folder / str(program)
                posts = []

                def transient_then_success(request):
                    posts.append(request.method)
                    if len(posts) % 3:
                        return httpx.Response(503, json={"error": {"message": "transient"}})
                    return normal_response(request)

                self.response = transient_then_success
                with self.assertRaisesRegex(ValueError, "50-request limit"):
                    self.collect(program)
                calls = json.loads((self.run_dir / "requests.json").read_text())["api_requests"]
                self.assertEqual(posts, ["POST"] * 50)
                self.assertEqual(Counter(c["status"] for c in calls), {"completed": 16, "failed": 34})
                self.assertFalse((self.run_dir / "executions").exists())

    def test_cap_before_first_repair_post_and_between_retries(self):
        normal_response = self.response
        for dispatched_repair in (False, True):
            with self.subTest(dispatched_repair=dispatched_repair):
                self.run_dir = self.folder / f"cap-{dispatched_repair}"
                self.posts.clear()
                self.failures = 1
                observed = []
                initial_failures = 19 - int(dispatched_repair)

                def respond(request):
                    observed.append(request.method)
                    prompt = json.loads(request.content)["contents"][0]["parts"][0]["text"]
                    index = len(observed)
                    if "Previous code:" in prompt or (index < 2 * initial_failures and index % 2):
                        return httpx.Response(503, json={"error": {"message": "offline failure"}})
                    return normal_response(request)

                self.response = respond
                with self.assertRaisesRegex(ValueError, "50-request limit"):
                    self.collect(2)
                ledger = json.loads((self.run_dir / "requests.json").read_bytes())["api_requests"]
                self.assertEqual(observed, ["POST"] * 50)
                self.assertEqual(Counter(r["status"] for r in ledger), {"completed": 31, "failed": 19})
                history = CandidateHistory(self.run_dir / "code")
                rows = history.store.read_run(history.run_id)
                rejected, = [r for r in rows if r["type"] == "request_rejected"]
                repairs = [r for r in rows if r["type"] == "repair_request"]
                self.assertEqual(rejected["step_id"], "call_report")
                self.assertEqual(rejected["request_number"], 1 + int(dispatched_repair))
                self.assertEqual(len(repairs), int(dispatched_repair))
                self.assertEqual(sum(r["type"] == "request_error" for r in rows), 19)
                self.assertEqual(sum(r["type"] == "candidate" for r in rows), 31)
                if dispatched_repair:
                    self.assertEqual(rejected["repair_request_id"], repairs[0]["record_id"])
                    self.assertEqual(ledger[-1]["repair_request_id"], repairs[0]["record_id"])
                else:
                    self.assertEqual(rejected["execution_id"], rows[-2]["execution_id"])
                    self.assertIn("Previous code:", rejected["feedback"])
                self.assertEqual(sum(s["fix_attempts"] for s in self.exported()["steps"]), int(dispatched_repair))

    @unittest.skipUnless(os.getenv("ISSUE43_TEST_DOCKER") == "1", "requires local WSL/Linux Docker image")
    def test_real_interrupted_worker_stops_before_repair(self):
        self.worker_interrupt = True
        with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
            self.collect(0, real=True)
        self.assertEqual(sum(self.posts.values()), 33)
        run = self.exported()
        self.assertEqual((run["status"], run["outputs"]), ("ERROR", {}))
        self.assertTrue(all(s["first_try_success"] is None for s in run["steps"]))

    @unittest.skipUnless(os.getenv("ISSUE43_TEST_DOCKER") == "1", "requires local WSL/Linux Docker image")
    def test_real_isolation_and_repaired_fixtures(self):
        for program in (0, 2):
            with self.subTest(program=program):
                self.run_dir = self.folder / f"docker_{program}"
                self.posts.clear()
                self.failures = 1
                self.assertEqual(self.collect(program, real=True)["status"], "PASS")
                self.assertEqual(self.exported()["first_try_passed"], {0: 32, 2: 30}[program])
