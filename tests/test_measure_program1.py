"""Exercise live-run evidence collection with saved code and an offline SDK double."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from google import genai
from google.genai import types
from google.genai.types import GenerateContentResponseUsageMetadata

from src.measure_program1 import MeasuredTranslator, measure
from src.migration_results import main as export
from src.translation_history import LocalJSONHistoryStore


@unittest.skipUnless(os.getenv("ISSUE43_TEST_DOCKER") == "1", "requires local validator image and Docker")
class DockerProgram1Tests(unittest.TestCase):
    def setUp(self):
        # A synthetic sentinel in the parent must never reach the worker.
        env = {key: os.environ[key] for key in ("SystemRoot", "TEMP", "TMP") if key in os.environ}
        self.enterContext(patch("os.environ", {**env, "ISSUE43_PARENT_SENTINEL": "offline-only",
                                               "GEMINI_API_KEY": "synthetic", "GOOGLE_API_KEY": "synthetic"}))

    def review(self, files, approve):
        # Even a neighboring file in the approved directory must stay on the host.
        next(iter(files.values())).with_name(".env").write_text("OFFLINE_CANARY=synthetic")
        return approve

    macro_prefix = """
import os, socket
from pathlib import Path
assert os.getuid() == 65534
assert not {'ISSUE43_PARENT_SENTINEL', 'GOOGLE_API_KEY', 'GEMINI_API_KEY'} & os.environ.keys()
assert not any(os.access(p, os.R_OK) for p in ('/mnt/c', '/root/.config/gcloud', '/var/run/docker.sock', '/inputs/.git', '/inputs/.env'))
assert not Path('/inputs/code/.env').exists()
assert set(os.listdir('/sys/class/net')) == {'lo'}
with socket.socket() as probe:
    probe.settimeout(1)
    assert probe.connect_ex(('192.0.2.1', 443)) != 0
"""

    def run_case(self, *, repair, approve=True):
        fixture = Path(__file__).resolve().parent / "fixtures" / "program1_generated"
        code = {path.stem: path.read_text(encoding="utf-8") for path in fixture.glob("*.py")}
        code["grp_score_macro"] = self.macro_prefix + code["grp_score_macro"]
        usage = GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=2, total_token_count=17,
        )
        requests = []

        def generate(*, contents, **kwargs):
            requests.append(contents)
            if contents.startswith("{"):
                repair_call = json.loads(contents)
                targets = [step["id"] for step in repair_call["steps"]]
                self.assertCountEqual(targets, ["om", "grp_score_macro"])
                if len(requests) == 7:
                    raise TimeoutError("offline retry")
                payload = {step: code[step] for step in targets}
            else:
                step = next((key for key in code if f"Step ID: {key}\n" in contents), "grp_score_macro")
                source = code[step]
                if repair and step == "om":
                    source += '\nout_df["grp_score"] = 0.0'
                payload = {"python_code": source}
            return SimpleNamespace(text=json.dumps(payload), usage_metadata=usage if approve else None,
                                   candidates=[types.Candidate(finish_reason="STOP")])

        with (TemporaryDirectory() as temporary, patch("src.translator.genai.Client") as client,
              patch("src.translator.load_dotenv"), patch("src.translator.os") as settings):
            settings.environ.get.return_value = "offline-test"
            client.return_value.models.generate_content.side_effect = generate
            root = Path(temporary)
            run_dir = root / "measured"
            evidence = measure(MeasuredTranslator(), run_dir, lambda files: self.review(files, approve))
            self.assertEqual(len(requests), len(evidence["api_requests"]))
            for entry in evidence["histories"]:
                store = run_dir / f"attempt_{entry['attempt']}" / "code" / ".translation_history"
                records = LocalJSONHistoryStore(store).read_run(entry["run_id"])
                starts = [r for r in records if r["type"] == "execution_started"]
                if not approve:
                    self.assertEqual([], starts)
                    self.assertIsNone(entry["execution_id"])
                    continue
                start, = starts
                self.assertEqual(entry["execution_id"], start["execution_id"])
                outcomes = [r for r in records if r["type"] == "outcome"]
                self.assertEqual(6, len(outcomes))
                for outcome in outcomes:
                    self.assertEqual(start["execution_id"], outcome["execution_id"])
                    self.assertEqual(start["candidates"][outcome["result"]["id"]], outcome["candidate_id"])
            path = root / "demo" / "results.json"
            export(["--program1-run", str(run_dir), "--output", str(path)])
            result, = json.loads(path.read_text())["runs"]
            self.assertEqual(evidence["elapsed_seconds"], result["elapsed_seconds"])
            self.assertGreater(result["elapsed_seconds"], 0)
            self.assertIsNone(result["cost_usd"])
            self.assertEqual("gemini-3.5-flash-lite", result["configuration"]["model_name"])
            self.assertTrue(result["configuration"]["repair_loop"])
            self.assertEqual(result["id"], evidence["histories"][0]["run_id"])
            if approve and not repair:
                costs = root / "costs.json"
                costs.write_text(json.dumps({result["id"]: {"cost_usd": 0.0123}}), encoding="utf-8")
                export(["--program1-run", str(run_dir), "--measurements", str(costs),
                        "--output", str(path)])
                priced, = json.loads(path.read_text())["runs"]
                self.assertEqual({**result, "cost_usd": 0.0123}, priced)
            if repair:
                # Verification must catch a missing outcome, even when the summary says PASS.
                last = evidence["histories"][-1]
                store = run_dir / f"attempt_{last['attempt']}" / "code" / ".translation_history"
                for record_file in (store / last["run_id"]).glob("*/record.json"):
                    record = json.loads(record_file.read_text(encoding="utf-8"))
                    if record["type"] == "outcome":
                        record_file.parent.rename(record_file.parent.with_name(".interrupted"))
                        break
                with self.assertRaisesRegex(ValueError, "does not match"):
                    export(["--program1-run", str(run_dir), "--output", str(path)])
            return evidence, result

    def test_first_attempt_passes(self):
        evidence, result = self.run_case(repair=False)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(6, result["first_try_passed"])
        self.assertTrue(all(step["fix_attempts"] == 0 for step in result["steps"]))
        self.assertEqual(102, sum(call["usage"]["total_token_count"] for call in evidence["api_requests"]))

    def test_repaired_execution_preserves_first_attempt_and_counts_one_logical_fix(self):
        evidence, result = self.run_case(repair=True)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(5, result["first_try_passed"])
        self.assertEqual(6, result["first_try_measured"])
        self.assertEqual(5, len(result["outputs"]))
        self.assertTrue(all(table["status"] == "PASS" for table in result["outputs"].values()))
        steps = {step["id"]: step for step in result["steps"]}
        self.assertFalse(steps["om"]["first_try_success"])
        self.assertEqual("pass", steps["om"]["status"])
        self.assertEqual(["grp_score_macro", "om"], sorted(k for k, s in steps.items() if s["fix_attempts"] == 1))
        self.assertEqual(2, sum(s["fix_attempts"] for s in steps.values()))
        self.assertEqual(8, len(evidence["api_requests"]))
        self.assertIsNone(evidence["api_requests"][6]["usage"])
        self.assertEqual(17, evidence["api_requests"][7]["usage"]["total_token_count"])

    def test_declined_review_and_missing_usage_remain_unknown(self):
        evidence, result = self.run_case(repair=False, approve=False)
        self.assertEqual("REVIEW_REQUIRED", result["status"])
        self.assertEqual(0, result["first_try_measured"])
        self.assertIsNone(result["first_try_success_rate"])
        self.assertTrue(all(call["usage"] is None for call in evidence["api_requests"]))


class RequestEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.run_dir = Path(self.enterContext(TemporaryDirectory())) / "run"
        self.enterContext(patch("src.translator.load_dotenv"))
        self.enterContext(patch("src.translator.os"))
        client = self.enterContext(patch("src.translator.genai.Client"))
        self.validator = self.enterContext(patch("src.measure_program1.DockerValidator")).return_value
        client.return_value.models.generate_content.side_effect = self.generate
        self.service = MeasuredTranslator()

    def checkpoint(self):
        return json.loads((self.run_dir / "measurement.json").read_text(encoding="utf-8"))

    def generate(self, *, contents, **kwargs):
        checkpoint = self.checkpoint()
        call = checkpoint["api_requests"][-1]
        self.assertEqual("reserved", call["status"])
        self.assertIsNone(call["elapsed_seconds"])
        self.assertIsNone(call["usage"])
        payload = {"python_code": "pass"}
        if call["attempt"] == 2:
            self.assertCountEqual(["grp_score_macro", "om"], checkpoint["repair_requests"]["2"])
            self.assertEqual(checkpoint["repair_requests"]["2"], call["step_ids"])
            payload = {step["id"]: "pass" for step in json.loads(contents)["steps"]}
        return SimpleNamespace(text=json.dumps(payload), candidates=[types.Candidate(finish_reason="STOP")],
                               usage_metadata=GenerateContentResponseUsageMetadata(total_token_count=17))

    def test_interrupt_at_first_review_preserves_completed_calls(self):
        def interrupt(files):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            measure(self.service, self.run_dir, interrupt)
        saved = self.checkpoint()
        self.assertEqual("INCOMPLETE", saved["status"])
        self.assertIsNone(saved["elapsed_seconds"])
        calls = saved["api_requests"]
        self.assertCountEqual(["grp_score_macro", "om", "os", "or", "ptexp", "process"],
                              [call["step_ids"][0] for call in calls])
        for call in calls:
            self.assertEqual((1, "completed", 17),
                             (call["attempt"], call["status"], call["usage"]["total_token_count"]))
            self.assertGreaterEqual(call["elapsed_seconds"], 0)
        self.validator.assert_not_called()

    def test_failed_repair_worker_preserves_last_validated_results(self):
        def validate(snapshot, plan, *args):
            if self.validator.call_count == 2:
                raise RuntimeError("repair worker stopped")
            steps, outputs = [], {}
            for item in plan:
                step = {"id": item["id"], "kind": item["kind"], "status": "SKIPPED"}
                if item["id"] in snapshot:
                    step.update(status="COMPLETE", generated_code=snapshot[item["id"]])
                if item["kind"] == "group":
                    passed = item["id"] != "om"
                    step.update(status="PASS" if passed else "FAIL", validation={"passed": passed})
                    outputs[item["group_config"]["output_table"]] = {
                        "step_id": item["id"], "status": step["status"], "validation": step["validation"],
                    }
                steps.append(step)
            return {"passed": False, "status": "FAIL", "steps": steps, "outputs": outputs}

        self.validator.side_effect = validate
        measure(self.service, self.run_dir, lambda _: True)
        evidence = self.checkpoint()
        first, failed = evidence["histories"]
        self.assertEqual("FAIL", first["status"])
        self.assertEqual(2, failed["attempt"])
        self.assertIsNone(failed["status"])
        self.assertTrue(failed["execution_id"])
        self.assertNotEqual(first["execution_id"], failed["execution_id"])
        self.assertEqual("execution", evidence["error"]["stage"])
        self.assertEqual(7, len(evidence["api_requests"]))
        target = self.run_dir / "export.json"
        export(["--program1-run", str(self.run_dir), "--output", str(target)])
        result, = json.loads(target.read_text())["runs"]
        self.assertEqual("ERROR", result["status"])
        self.assertEqual((5, 6), (result["first_try_passed"], result["first_try_measured"]))
        self.assertCountEqual(["FAIL"] + ["PASS"] * 4, [t["status"] for t in result["outputs"].values()])
        for step in result["steps"]:
            self.assertEqual(int(step["id"] in {"om", "grp_score_macro"}), step["fix_attempts"])
            self.assertEqual(step["id"] != "om", step["first_try_success"])
            self.assertEqual({"om": "fail", "grp_score_macro": "complete"}.get(step["id"], "pass"),
                             step["status"])


class IsolationFailureTests(unittest.TestCase):
    def test_preflight_failure_prevents_generation(self):
        with (TemporaryDirectory() as tmp,
              patch("src.measure_program1.DockerValidator", side_effect=RuntimeError("unavailable")),
              patch("src.measure_program1.run_program1_with_feedback") as loop):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                measure(None, Path(tmp) / "run", lambda _: True)
            loop.assert_not_called()
            self.assertFalse((Path(tmp) / "run").exists())

    def test_worker_failure_ends_attempt_before_repair(self):
        with (TemporaryDirectory() as tmp, patch("src.measure_program1.DockerValidator") as factory,
              patch("src.translator.load_dotenv"), patch("src.translator.os"),
              patch("src.translator.genai.Client") as client):
            client.return_value.models.generate_content.return_value = SimpleNamespace(
                text='{"python_code":"pass"}', candidates=[types.Candidate(finish_reason="STOP")],
                usage_metadata=None,
            )
            factory.return_value.side_effect = RuntimeError("container stopped")
            report = measure(MeasuredTranslator(), Path(tmp) / "run", lambda _: True)
            self.assertEqual("ERROR", report["status"])
            self.assertEqual("execution", report["error"]["stage"])
            self.assertEqual("container stopped", report["error"]["message"])
            self.assertEqual({}, report["repair_requests"])
            self.assertEqual(6, len(report["api_requests"]))
            factory.return_value.assert_called_once()


class MeasuredSdkTests(unittest.TestCase):
    def setUp(self):
        # These responses stop in generation, before validation.
        self.enterContext(patch("src.measure_program1.DockerValidator"))

    def service(self, handler):
        # Run the installed SDK and its HTTP machinery. Only the transport is fake.
        sdk_client = genai.Client

        def offline_client(*, api_key, http_options):
            http_options.client_args["transport"] = httpx.MockTransport(handler)
            return sdk_client(api_key=api_key, vertexai=False, http_options=http_options)

        with (patch("src.translator.genai.Client", side_effect=offline_client),
              patch("src.translator.load_dotenv"),
              patch("os.environ", {"GEMINI_API_KEY": "offline-test"})):
            service = MeasuredTranslator()
        self.addCleanup(service.client.close)
        return service

    def test_partial_generation_is_recorded_once_and_never_reviewed(self):
        for reason, token in (("CONTINUATION", "YWJj"), ("CONTINUATION", None),
                              (None, "YWJj"), ("STOP", "YWJj"), ("MAX_TOKENS", None)):
            with self.subTest(reason=reason, token=token), TemporaryDirectory() as tmp:
                posts = []

                def send(request):
                    posts.append(request)
                    self.assertEqual(1, len(posts), "SDK silently followed up")
                    # Valid JSON and Python: parsing alone would accept this partial answer.
                    candidate = {"content": {"parts": [{"text": '{"python_code":"pass"}'}]}}
                    if reason:
                        candidate["finishReason"] = reason
                    if token:
                        candidate["continuationToken"] = token
                    return httpx.Response(200, json={
                        "candidates": [candidate], "usageMetadata": {"totalTokenCount": 11},
                    })

                root = Path(tmp) / "run"
                report = measure(self.service(send), root, lambda _: self.fail("partial code reached review"))
                self.assertEqual(["POST"], [r.method for r in posts])
                self.assertEqual("ERROR", report["status"])
                self.assertEqual("generation", report["error"]["stage"])
                self.assertRegex(report["error"]["message"], "continuation|Incomplete Gemini")
                self.assertEqual(11, report["api_requests"][0]["usage"]["total_token_count"])
                self.assertFalse(list(root.rglob("*.py")))

    def test_transient_failures_use_three_recorded_application_attempts(self):
        posts = []

        def send(request):
            posts.append(request)
            return httpx.Response(503, json={"error": {"message": "offline service unavailable"}})

        with TemporaryDirectory() as tmp, patch("src.pipeline_retry.time.sleep"):
            report = measure(self.service(send), Path(tmp) / "run",
                             lambda _: self.fail("failed request reached review"))
        self.assertEqual("ERROR", report["status"])
        self.assertEqual(3, len(posts))
        self.assertEqual(3, len(report["api_requests"]))
        self.assertEqual(3, report["error"]["attempts"])

    def test_partial_repair_is_rejected_before_replacing_code(self):
        posts = []

        def send(request):
            posts.append(request)
            self.assertEqual(1, len(posts), "SDK continued a repair")
            return httpx.Response(200, json={"candidates": [{
                "finishReason": "CONTINUATION", "continuationToken": "YWJj",
                "content": {"parts": [{"text": '{"om":"pass"}'}]},
            }]})

        service = self.service(send)
        service.attempt = 2
        with self.assertRaisesRegex(ValueError, "continuation"):
            service.repair_program1([{"id": "om"}], {"om": "previous code"}, {})
        self.assertEqual(1, len(posts))
        self.assertEqual({2: ["om"]}, service.repairs)

    def test_sdk_does_not_replay_redirects_or_retry_service_errors(self):
        for status in (307, 503):
            with self.subTest(status=status):
                posts = []

                def send(request):
                    posts.append(request)
                    self.assertEqual(1, len(posts), "SDK sent another request")
                    return httpx.Response(status, headers={"Location": "/another-generation"},
                                          json={"error": {"message": "offline failure"}})

                service = self.service(send)
                with self.assertRaises(genai.errors.APIError):
                    service.translate_macro({"id": "macro"})
                self.assertEqual(["POST"], [r.method for r in posts])
                self.assertEqual(1, len(service.requests))

    def test_completed_response_and_hard_request_limit(self):
        posts = []

        def send(request):
            posts.append(request)
            return httpx.Response(200, json={"candidates": [{
                "finishReason": "STOP", "content": {"parts": [{"text": '{"python_code":"pass"}'}]},
            }]})

        service = self.service(send)
        for _ in range(24):
            self.assertEqual("pass", service.translate_macro({"id": "macro"}))
        with self.assertRaisesRegex(ValueError, "24-request limit"):
            service.translate_macro({"id": "macro"})
        self.assertEqual(24, len(posts))
        self.assertEqual(24, len(service.requests))
