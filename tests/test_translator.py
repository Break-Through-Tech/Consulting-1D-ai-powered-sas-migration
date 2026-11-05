import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
from src import translator


class FakeTranslator:
    def __init__(self, *args, **kwargs):
        pass

    def translate_macro(self, step):
        return "def grp_score(df, varlist):\n    return df.copy()"

    def translate_group_step(self, step, measures):
        if step["id"] == "om":
            return "out_df = input_df.copy()"
        return "pass"


class TranslatorTests(unittest.TestCase):
    def test_repair_prompt_contains_previous_code_sas_and_feedback(self):
        client = MagicMock()
        client.models.generate_content.return_value.text = json.dumps({
            "grp_score_macro": "def grp_score(df, varlist): return df",
        })
        with patch.object(translator.genai, "Client", return_value=client):
            service = translator.GeminiSASTranslator(api_key="fake-key")
        steps = [{"id": "grp_score_macro", "sas_code": "%macro grp_score;"}]
        previous = {"grp_score_macro": "old code"}
        feedback = {"OUTCOME_SAFETY": {"passed": False, "mismatches": {"score": {"count": 2}}}}
        repaired = service.repair_program1(steps, previous, feedback)
        prompt = json.loads(client.models.generate_content.call_args.kwargs["contents"])
        self.assertEqual(prompt["steps"], steps)
        self.assertEqual(prompt["previous_code"], previous)
        self.assertEqual(prompt["validation_feedback"], feedback)
        self.assertIn("grp_score_macro", repaired)

    def test_repair_rejects_invalid_response(self):
        service = translator.GeminiSASTranslator.__new__(translator.GeminiSASTranslator)
        service.client = MagicMock()
        service.model_name = "fake"
        for response in ({}, {"macro": ""}, {"macro": 42}, []):
            with self.subTest(response=response):
                service.client.models.generate_content.return_value.text = json.dumps(response)
                with self.assertRaises(ValueError):
                    service.repair_program1([{"id": "macro"}], {}, {})

    def test_missing_output_does_not_reuse_previous_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sas_dir = root / "sas"
            sas_dir.mkdir()

            pd.DataFrame({
                "PROVIDER_ID": ["001234"],
                "M1": [3.0],
            }).to_csv(
                sas_dir / "STD_DATA_2025JUL_ANALYSIS.csv",
                index=False,
            )

            manifest = [
                {"id": "macro", "kind": "macro", "translate": True},
                {
                    "id": "om", "kind": "group", "translate": True,
                    "outputs": ["OUTCOME_MORTALITY"],
                },
                {
                    "id": "os", "kind": "group", "translate": True,
                    "outputs": ["OUTCOME_SAFETY"],
                },
            ]

            config = {
                "OM": {
                    "output_table": "OUTCOME_MORTALITY",
                    "measures": ["M1"],
                },
                "OS": {
                    "output_table": "OUTCOME_SAFETY",
                    "measures": ["M1"],
                },
            }

            manifest_path = root / "manifest.json"
            config_path = root / "config.json"
            manifest_path.write_text(json.dumps(manifest))
            config_path.write_text(json.dumps(config))

            data_dir = root / "output" / "data"

            with (
                patch.object(translator, "GeminiSASTranslator", FakeTranslator),
                patch.object(translator, "DEFAULT_MANIFEST", manifest_path),
                patch.object(translator, "DEFAULT_GROUP_CONFIG", config_path),
                patch.object(translator, "DEFAULT_SAS_OUTPUT_DIR", sas_dir),
                patch.object(translator, "OUTPUT_DATA_DIR", data_dir),
                patch.object(
                    translator, "OUTPUT_CODE_DIR", root / "output" / "code"
                ),
            ):
                translator.run_translator()

            self.assertTrue(
                (data_dir / "OUTCOME_MORTALITY.csv").exists()
            )
            self.assertFalse(
                (data_dir / "OUTCOME_SAFETY.csv").exists(),
                "The failed safety step reused mortality's output.",
            )


if __name__ == "__main__":
    unittest.main()
