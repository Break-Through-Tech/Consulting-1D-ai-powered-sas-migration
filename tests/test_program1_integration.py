import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd

from src.program1_integration import (
    generate_program1_code,
    run_reviewed_program1,
)
from src.translation_history import HistoryStorageError, LocalJSONHistoryStore


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifests" / "program1.json"
CONFIG = ROOT / "config" / "measure_groups.json"


class FakeTranslator:
    """Return trusted test code without contacting Gemini."""

    MACRO_CODE = """
def grp_score(df, varlist):
    return pd.DataFrame({
        "PROVIDER_ID": df["PROVIDER_ID"],
        "score": df[varlist].sum(axis=1),
    })
"""

    def __init__(self):
        self.calls = []

    def translate_macro(self, step):
        self.calls.append(step["id"])
        return self.MACRO_CODE

    def translate_group_step(self, step, measures):
        self.calls.append(step["id"])
        return "out_df = grp_score(input_df, measures)"


class Program1IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.root = Path(self.temp.name)
        self.code_dir = self.root / "reviewed_code"
        self.output_dir = self.root / "python_outputs"
        self.sas_dir = self.root / "sas_references"

        self.sas_dir.mkdir()

        self.groups = json.loads(CONFIG.read_text(encoding="utf-8"))
        measures = sorted({
            measure
            for group in self.groups.values()
            for measure in group["measures"]
        })

        self.provider_ids = ["001234", "009876"]

        input_data = {
            "PROVIDER_ID": self.provider_ids,
        }

        for measure in measures:
            input_data[measure] = [1.0, 3.0]

        pd.DataFrame(input_data).to_csv(
            self.root / "input.csv",
            index=False,
        )

        # Synthetic references matching the trusted fake macro.
        for group in self.groups.values():
            count = len(group["measures"])
            expected = pd.DataFrame({
                "PROVIDER_ID": self.provider_ids,
                "score": [float(count), float(3 * count)],
            })
            expected.to_csv(
                self.sas_dir / f"{group['output_table']}.csv",
                index=False,
            )

        self.translator = FakeTranslator()

    def generate(self):
        return generate_program1_code(
            self.translator,
            code_dir=self.code_dir,
            manifest_path=MANIFEST,
            config_path=CONFIG,
        )

    def execute(self):
        return run_reviewed_program1(
            code_dir=self.code_dir,
            input_csv=self.root / "input.csv",
            output_dir=self.output_dir,
            manifest_path=MANIFEST,
            config_path=CONFIG,
            sas_output_dir=self.sas_dir,
        )

    def test_generation_creates_all_six_files_without_execution(self):
        generated = self.generate()

        self.assertEqual(
            set(generated),
            {"grp_score_macro", "om", "os", "or", "ptexp", "process"},
        )
        self.assertTrue(all(path.is_file() for path in generated.values()))
        self.assertFalse(self.output_dir.exists())

        self.assertEqual(
            self.translator.calls,
            ["grp_score_macro", "om", "os", "or", "ptexp", "process"],
        )

    def history(self):
        pointer = json.loads((self.code_dir / "translation_history.json").read_text())
        return LocalJSONHistoryStore(self.code_dir / ".translation_history").read_run(pointer["run_id"])

    def test_unchanged_crlf_code_retains_candidate_ids_and_exact_utf8(self):
        self.translator.MACRO_CODE = self.translator.MACRO_CODE.replace("\n", "\r\n")
        group_code = "# café\r\nout_df = grp_score(input_df, measures)\r\n"
        with patch.object(self.translator, "translate_group_step", return_value=group_code):
            files = self.generate()
        before = {r["step_id"]: r for r in self.history() if r["type"] == "candidate"}
        for step_id, path in files.items():
            self.assertEqual(path.read_bytes(), before[step_id]["code"].encode("utf-8"))
        self.assertTrue(self.execute()["passed"])
        records = self.history()
        self.assertEqual(len([r for r in records if r["type"] == "candidate"]), 6)
        for outcome in (r for r in records if r["type"] == "outcome"):
            self.assertEqual(outcome["candidate_id"], before[outcome["result"]["id"]]["record_id"])
            self.assertEqual(outcome["result"]["generated_code"], before[outcome["result"]["id"]]["code"])

    def test_each_execution_preserves_changed_configuration_at_same_path(self):
        config = self.root / "groups.json"
        config.write_text(json.dumps(self.groups))
        generate_program1_code(self.translator, self.code_dir, config_path=config)

        def execute():
            return run_reviewed_program1(self.code_dir, self.root / "input.csv", self.output_dir,
                                         config_path=config, sas_output_dir=self.sas_dir)

        self.assertTrue(execute()["passed"])
        changed = json.loads(config.read_text())
        changed["OM"]["measures"] = changed["OM"]["measures"][:1]
        config.write_text(json.dumps(changed))
        self.assertFalse(execute()["passed"])
        records = self.history()
        starts = [r for r in records if r["type"] == "execution_started"]
        self.assertEqual(starts[0]["candidates"], starts[1]["candidates"])
        self.assertEqual(starts[0]["metadata"]["config_path"], starts[1]["metadata"]["config_path"])
        for start, expected in zip(starts, (self.groups, changed)):
            om = next(step for step in start["execution_plan"] if step["id"] == "om")
            self.assertEqual(om["group_config"], expected["OM"])
            outcome = next(r for r in records if r["type"] == "outcome"
                           and r["execution_id"] == start["execution_id"] and r["result"]["id"] == "om")
            self.assertEqual(outcome["candidate_id"], start["candidates"]["om"])
        original = next(r for r in records if r["type"] == "candidate" and r["step_id"] == "om")
        self.assertEqual(original["step"]["group_config"], self.groups["OM"])
        self.assertEqual(len([r for r in records if r["type"] == "candidate"]), 6)

    def test_execution_uses_recorded_plan_if_config_changes_after_snapshot(self):
        config = self.root / "groups.json"
        config.write_text(json.dumps(self.groups))
        generate_program1_code(self.translator, self.code_dir, config_path=config)
        original_append = LocalJSONHistoryStore.append

        def change_config(store, run_id, record, **kwargs):
            record_id = original_append(store, run_id, record, **kwargs)
            if record["type"] == "execution_started":
                changed = json.loads(config.read_text())
                changed["OM"]["measures"] = changed["OM"]["measures"][:1]
                config.write_text(json.dumps(changed))
            return record_id

        with patch.object(LocalJSONHistoryStore, "append", change_config):
            result = run_reviewed_program1(self.code_dir, self.root / "input.csv", self.output_dir,
                                          config_path=config, sas_output_dir=self.sas_dir)
        self.assertTrue(result["passed"])

    def test_output_directory_error_is_recorded_before_reraising(self):
        self.generate()
        self.execute()
        original_mkdir = Path.mkdir
        for mode in ("raise", "report"):
            failure = PermissionError("directory denied")

            def deny_output(path, *args, **kwargs):
                if path == self.output_dir:
                    raise failure
                return original_mkdir(path, *args, **kwargs)

            with self.subTest(mode=mode), patch.object(Path, "mkdir", deny_output):
                with self.assertRaises(PermissionError) as caught:
                    run_reviewed_program1(self.code_dir, self.root / "input.csv", self.output_dir,
                                         sas_output_dir=self.sas_dir, on_error=mode)
            self.assertIs(caught.exception, failure)
            records = self.history()
            start = [r for r in records if r["type"] == "execution_started"][-1]
            error = records[-1]
            self.assertEqual(error["type"], "execution_error")
            self.assertEqual(error["execution_id"], start["execution_id"])
            self.assertEqual(error["operation"], "create_output_directory")
            self.assertEqual(error["error"]["type"], "PermissionError")
            self.assertFalse(any(r["type"] == "outcome" and r["execution_id"] == start["execution_id"] for r in records))

    def test_stale_output_deletion_error_is_recorded_before_reraising(self):
        self.generate()
        self.execute()
        stale = self.output_dir / "OUTCOME_MORTALITY.csv"
        original_unlink = Path.unlink
        for mode in ("raise", "report"):
            failure = PermissionError("CSV locked")

            def deny_stale(path, *args, **kwargs):
                if path == stale:
                    raise failure
                return original_unlink(path, *args, **kwargs)

            with self.subTest(mode=mode), patch.object(Path, "unlink", deny_stale):
                with self.assertRaises(PermissionError) as caught:
                    run_reviewed_program1(self.code_dir, self.root / "input.csv", self.output_dir,
                                         sas_output_dir=self.sas_dir, on_error=mode)
            self.assertIs(caught.exception, failure)
            records = self.history()
            error = records[-1]
            start = [r for r in records if r["type"] == "execution_started"][-1]
            self.assertEqual(error["type"], "execution_error")
            self.assertEqual(error["execution_id"], start["execution_id"])
            self.assertEqual(error["operation"], "remove_stale_output")
            self.assertEqual(error["path"], str(stale))
            self.assertEqual(error["error"]["message"], "CSV locked")
            self.assertFalse(any(r["type"] == "outcome" and r["execution_id"] == start["execution_id"] for r in records))

    def test_generation_preserves_pending_candidates_without_validation(self):
        self.translator.model_name = "fake-model"
        files = self.generate()
        records = self.history()
        self.assertEqual(records[0]["metadata"]["model_name"], "fake-model")
        candidates = [r for r in records if r["type"] == "candidate"]
        self.assertEqual(len(candidates), 6)
        for candidate in candidates:
            self.assertEqual(candidate["review_status"], "pending")
            self.assertEqual(candidate["code"], files[candidate["step_id"]].read_text())
            self.assertNotIn("validation", candidate)
        self.assertFalse(any(r["type"] == "outcome" for r in records))

    def test_repeated_execution_retains_both_outcomes_without_new_candidates(self):
        self.generate()
        self.execute()
        self.execute()
        records = self.history()
        candidates = {r["record_id"] for r in records if r["type"] == "candidate"}
        outcomes = [r for r in records if r["type"] == "outcome"]
        self.assertEqual(len(candidates), 6)
        self.assertEqual(len(outcomes), 12)
        self.assertEqual(len({r["execution_id"] for r in outcomes}), 2)
        self.assertTrue(all(r["candidate_id"] in candidates for r in outcomes))
        for outcome in outcomes:
            if outcome["result"]["kind"] == "group":
                self.assertIs(outcome["result"]["validation"]["passed"], True)
            else:
                self.assertEqual(outcome["validation_status"], "not_applicable")

    def test_review_edit_creates_linked_candidate_and_preserves_original(self):
        self.generate()
        original = next(r for r in self.history() if r.get("step_id") == "os")
        changed = "out_df = grp_score(input_df, measures)\nout_df['score'] += 1\n"
        (self.code_dir / "os.py").write_bytes(changed.encode("utf-8"))
        result = self.execute()
        self.assertFalse(result["passed"])
        versions = [r for r in self.history() if r["type"] == "candidate" and r["step_id"] == "os"]
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["code"], original["code"])
        self.assertEqual(versions[1]["code"], changed)
        self.assertEqual(versions[1]["previous_candidate_id"], original["record_id"])
        outcome = next(r for r in self.history() if r["type"] == "outcome" and r["result"]["id"] == "os")
        self.assertEqual(outcome["candidate_id"], versions[1]["record_id"])
        self.assertEqual(outcome["result"]["status"], "FAIL")

    def test_request_failure_does_not_create_candidate(self):
        with patch.object(self.translator, "translate_macro", side_effect=TimeoutError("offline")):
            with self.assertRaises(Exception):
                generate_program1_code(self.translator, self.code_dir, max_attempts=2,
                                       retry_delay_seconds=0)
        records = self.history()
        self.assertEqual([r["type"] for r in records], ["run", "request_error", "request_error"])
        self.assertEqual([r["request_number"] for r in records[1:]], [1, 2])
        self.assertTrue(all("candidate_id" not in r for r in records))

    def test_partial_generation_keeps_successes_before_request_failure(self):
        with patch.object(self.translator, "translate_group_step", side_effect=ValueError("invalid request")):
            with self.assertRaises(ValueError):
                self.generate()
        records = self.history()
        self.assertEqual([r["type"] for r in records], ["run", "candidate", "request_error"])
        self.assertEqual(records[1]["step_id"], "grp_score_macro")

    def test_transient_request_failure_then_success_creates_only_one_candidate(self):
        with patch.object(self.translator, "translate_macro", side_effect=[
            TimeoutError("offline"), self.translator.MACRO_CODE,
        ]):
            generate_program1_code(self.translator, self.code_dir, retry_delay_seconds=0)
        records = self.history()
        self.assertEqual(len([r for r in records if r["type"] == "candidate"]), 6)
        self.assertEqual(len([r for r in records if r["type"] == "request_error"]), 1)
        self.assertNotIn("model_name", records[0]["metadata"])

    def test_backend_timeout_does_not_retry_the_model_request(self):
        original_append = LocalJSONHistoryStore.append

        def fail_request_record(store, run_id, record, **kwargs):
            if record["type"] == "request_error":
                raise TimeoutError("storage unavailable")
            return original_append(store, run_id, record, **kwargs)

        with patch.object(self.translator, "translate_macro", side_effect=TimeoutError("offline")) as request:
            with patch.object(LocalJSONHistoryStore, "append", fail_request_record):
                with self.assertRaisesRegex(HistoryStorageError, "storage unavailable"):
                    generate_program1_code(self.translator, self.code_dir, retry_delay_seconds=0)
        self.assertEqual(request.call_count, 1)

    def test_existing_reviewed_files_are_imported_without_model_metadata(self):
        files = self.generate()
        imported = self.root / "existing_code"
        imported.mkdir()
        for path in files.values():
            (imported / path.name).write_text(path.read_text())
        result = run_reviewed_program1(imported, self.root / "input.csv", self.output_dir,
                                      sas_output_dir=self.sas_dir)
        self.assertTrue(result["passed"])
        pointer = json.loads((imported / "translation_history.json").read_text())
        records = LocalJSONHistoryStore(imported / ".translation_history").read_run(pointer["run_id"])
        self.assertNotIn("model_name", records[0]["metadata"])
        candidates = [r for r in records if r["type"] == "candidate"]
        self.assertEqual(len(candidates), 6)
        self.assertTrue(all(r["origin"] == "reviewed_file" for r in candidates))

    def test_execution_exception_is_persisted_before_raise(self):
        self.generate()
        (self.code_dir / "os.py").write_text("raise RuntimeError('intentional')")
        with self.assertRaisesRegex(RuntimeError, "intentional"):
            self.execute()
        outcomes = [r for r in self.history() if r["type"] == "outcome"]
        self.assertEqual(outcomes[-1]["result"]["stage"], "execution")
        self.assertEqual(outcomes[-1]["result"]["error"]["type"], "RuntimeError")
        self.assertEqual(outcomes[-1]["validation_status"], "not_completed")

    def test_validation_exception_and_blocked_steps_are_persisted(self):
        self.generate()
        with patch("src.program1_integration.compare_detailed", side_effect=ValueError("invalid reference")):
            with self.assertRaisesRegex(ValueError, "invalid reference"):
                self.execute()
        self.assertEqual(self.history()[-1]["result"]["stage"], "validation")
        (self.code_dir / "grp_score_macro.py").write_text("raise RuntimeError('macro error')")
        run_reviewed_program1(self.code_dir, self.root / "input.csv", self.output_dir,
                             sas_output_dir=self.sas_dir, on_error="report")
        outcomes = [r for r in self.history() if r["type"] == "outcome"][-6:]
        self.assertEqual(outcomes[0]["result"]["status"], "ERROR")
        self.assertEqual([r["result"]["status"] for r in outcomes[1:]], ["BLOCKED"] * 5)

    def test_execution_uses_archived_snapshot_if_working_file_changes(self):
        self.generate()
        original_append = LocalJSONHistoryStore.append

        def change_file(store, run_id, record, **kwargs):
            record_id = original_append(store, run_id, record, **kwargs)
            if record["type"] == "execution_started":
                (self.code_dir / "os.py").write_text("raise RuntimeError('changed after snapshot')")
            return record_id

        with patch.object(LocalJSONHistoryStore, "append", change_file):
            self.assertTrue(self.execute()["passed"])

    def test_storage_error_propagates_even_with_report_mode(self):
        self.generate()
        original_append = LocalJSONHistoryStore.append

        def fail_outcome(store, run_id, record, **kwargs):
            if record["type"] == "outcome":
                raise OSError("disk full")
            return original_append(store, run_id, record, **kwargs)

        with patch.object(LocalJSONHistoryStore, "append", fail_outcome):
            with self.assertRaisesRegex(OSError, "disk full"):
                run_reviewed_program1(self.code_dir, self.root / "input.csv", self.output_dir,
                                     sas_output_dir=self.sas_dir, on_error="report")

    def test_custom_backend_needs_no_local_history_artifacts(self):
        class MemoryStore:
            def __init__(self):
                self.records = []

            def create_run(self, metadata):
                self.append("opaque-run", {"type": "run", "metadata": metadata})
                return "opaque-run"

            def append(self, run_id, record, *, code=None):
                from src.translation_history import code_hash
                record = {**record, "record_id": str(len(self.records))}
                if code is not None:
                    record.update(code=code, code_sha256=code_hash(code))
                self.records.append(record)
                return record["record_id"]

            def read_run(self, run_id):
                return self.records.copy()

        store = MemoryStore()
        generate_program1_code(self.translator, self.code_dir, history_store=store)
        result = run_reviewed_program1(self.code_dir, self.root / "input.csv", self.output_dir,
                                      sas_output_dir=self.sas_dir, history_store=store)
        self.assertTrue(result["passed"])
        self.assertFalse((self.code_dir / ".translation_history").exists())
        self.assertEqual(len([r for r in store.records if r["type"] == "candidate"]), 6)
        self.assertEqual(len([r for r in store.records if r["type"] == "outcome"]), 6)

    def test_all_five_groups_pass_validation(self):
        self.generate()
        result = self.execute()

        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["outputs"]), 5)

        for group in self.groups.values():
            table = group["output_table"]
            self.assertEqual(result["outputs"][table]["status"], "PASS")
            self.assertTrue((self.output_dir / f"{table}.csv").exists())

        self.assertEqual(result["steps"][0]["status"], "SKIPPED")

    def test_sas_mismatch_is_reported(self):
        self.generate()

        table = "OUTCOME_SAFETY"
        path = self.sas_dir / f"{table}.csv"
        reference = pd.read_csv(path, dtype={"PROVIDER_ID": "string"})
        reference.loc[0, "score"] += 100
        reference.to_csv(path, index=False)

        result = self.execute()

        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["outputs"][table]["status"], "FAIL")

    def test_missing_reviewed_code_stops_before_execution(self):
        self.generate()
        (self.code_dir / "os.py").unlink()

        with self.assertRaises(FileNotFoundError):
            self.execute()

        self.assertFalse(self.output_dir.exists())

    def test_reviewed_run_reports_execution_failure(self):
        self.generate()

        (self.code_dir / "os.py").write_text(
            "raise RuntimeError('intentional execution failure')",
            encoding="utf-8",
        )

        result = run_reviewed_program1(
            code_dir=self.code_dir,
            input_csv=self.root / "input.csv",
            output_dir=self.output_dir,
            manifest_path=MANIFEST,
            config_path=CONFIG,
            sas_output_dir=self.sas_dir,
            on_error="report",
        )

        self.assertEqual("FAIL", result["status"])
        self.assertFalse(result["passed"])

        safety = result["outputs"]["OUTCOME_SAFETY"]
        self.assertEqual("ERROR", safety["status"])
        self.assertEqual("RuntimeError", safety["error"]["type"])

        safety_step = next(
            step for step in result["steps"]
            if step["id"] == "os"
        )
        self.assertEqual("execution", safety_step["stage"])

        for table in (
            "OUTCOME_MORTALITY",
            "OUTCOME_READMISSION",
            "PTEXP",
            "PROCESS",
        ):
            self.assertEqual(
                "PASS",
                result["outputs"][table]["status"],
            )

    def test_non_dataframe_output_is_rejected(self):
        self.generate()

        (self.code_dir / "os.py").write_text(
            "out_df = 42",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(TypeError, "DataFrame"):
            self.execute()

    def test_transient_translation_failure_is_retried(self):
        class FlakyTranslator(FakeTranslator):
            def translate_macro(self, step):
                self.calls.append(step["id"])
                if self.calls.count(step["id"]) == 1:
                    raise TimeoutError("temporary translation timeout")
                return self.MACRO_CODE

        self.translator = FlakyTranslator()

        with patch("src.pipeline_retry.time.sleep"):
            generated = self.generate()

        self.assertEqual(len(generated), 6)
        self.assertEqual(
            self.translator.calls.count("grp_score_macro"),
            2,
        )

    def test_permanent_translation_error_is_not_retried(self):
        class InvalidTranslator(FakeTranslator):
            def translate_macro(self, step):
                self.calls.append(step["id"])
                raise ValueError("invalid translation input")

        self.translator = InvalidTranslator()

        with self.assertRaisesRegex(
            ValueError,
            "invalid translation input",
        ):
            self.generate()

        self.assertEqual(
            ["grp_score_macro"],
            self.translator.calls,
        )

    def test_generation_does_not_overwrite_existing_code(self):
        self.generate()

        with self.assertRaises(FileExistsError):
            self.generate()


    def test_each_group_receives_independent_input_copy(self):
        self.generate()

        # Mortality changes a Safety measure that it does not use.
        # Safety must still receive the original input data.
        safety_measure = self.groups["OS"]["measures"][0]
        self.assertNotIn(
            safety_measure,
            self.groups["OM"]["measures"],
        )

        (self.code_dir / "om.py").write_text(
            f"input_df[{safety_measure!r}] = -999.0\n"
            "out_df = grp_score(input_df, measures)\n",
            encoding="utf-8",
        )

        result = self.execute()

        self.assertTrue(result["passed"])
        self.assertEqual(
            result["outputs"]["OUTCOME_SAFETY"]["status"],
            "PASS",
        )

    def test_stale_outputs_cleared_before_execution_failure(self):
        self.generate()
        self.output_dir.mkdir()

        # Simulate CSVs remaining from an earlier successful run.
        for group in self.groups.values():
            table = group["output_table"]
            (self.output_dir / f"{table}.csv").write_text(
                "stale output\n",
                encoding="utf-8",
            )

        # Mortality runs first; Safety then fails deliberately.
        (self.code_dir / "os.py").write_text(
            "raise RuntimeError('intentional test failure')\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "intentional test failure",
        ):
            self.execute()

        # Mortality produced a fresh CSV before the failure.
        mortality = self.output_dir / "OUTCOME_MORTALITY.csv"
        self.assertTrue(mortality.exists())
        self.assertNotEqual(
            mortality.read_text(encoding="utf-8"),
            "stale output\n",
        )

        # The remaining groups must not retain stale CSVs.
        for group in self.groups.values():
            table = group["output_table"]
            if table != "OUTCOME_MORTALITY":
                self.assertFalse(
                    (self.output_dir / f"{table}.csv").exists()
                )


if __name__ == "__main__":
    unittest.main()
