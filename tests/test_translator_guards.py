import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src import translator


class TranslatorGuardTests(unittest.TestCase):

    def run_fake(self, group_code, provider_ids=None, values=None, stale_outputs=None):
        """Run the translator with fake Gemini responses and temporary files."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)

        sas_dir = root / "sas"
        sas_dir.mkdir()

        if provider_ids is None:
            provider_ids = ["001234"]
        if values is None:
            values = [3.0]

        pd.DataFrame({
            "PROVIDER_ID": provider_ids,
            "M1": values,
        }).to_csv(
            sas_dir / "STD_DATA_2025JUL_ANALYSIS.csv",
            index=False,
        )

        tables = {
            "om": "OUTCOME_MORTALITY",
            "os": "OUTCOME_SAFETY",
        }

        manifest = [
            {"id": "macro", "kind": "macro", "translate": True}
        ]
        config = {}

        for group_id in group_code:
            output_table = tables[group_id]

            manifest.append({
                "id": group_id,
                "kind": "group",
                "translate": True,
                "outputs": [output_table],
            })

            config[group_id.upper()] = {
                "output_table": output_table,
                "measures": ["M1"],
            }

        manifest_path = root / "manifest.json"
        config_path = root / "config.json"

        manifest_path.write_text(json.dumps(manifest))
        config_path.write_text(json.dumps(config))

        self.data_dir = root / "output" / "data"

        if stale_outputs:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            for name, dataframe in stale_outputs.items():
                dataframe.to_csv(
                    self.data_dir / f"{name}.csv",
                    index=False,
                )

        class FakeTranslator:
            def __init__(self, *args, **kwargs):
                pass

            def translate_macro(self, step):
                return (
                    "def grp_score(df, varlist):\n"
                    "    return df.copy()"
                )

            def translate_group_step(self, step, measures):
                return group_code[step["id"]]

        with (
            patch.object(translator, "GeminiSASTranslator", FakeTranslator),
            patch.object(translator, "DEFAULT_MANIFEST", manifest_path),
            patch.object(translator, "DEFAULT_GROUP_CONFIG", config_path),
            patch.object(translator, "DEFAULT_SAS_OUTPUT_DIR", sas_dir),
            patch.object(translator, "OUTPUT_DATA_DIR", self.data_dir),
            patch.object(
                translator,
                "OUTPUT_CODE_DIR",
                root / "output" / "code",
            ),
        ):
            return translator.run_translator()

    def test_previous_group_variables_do_not_leak(self):
        """Safety must not be able to use Mortality's helper variables."""
        with self.assertRaisesRegex(NameError, "mort"):
            self.run_fake({
                "om": "mort = input_df.copy()\nout_df = mort",
                "os": "out_df = mort",
            })

        self.assertFalse(
            (self.data_dir / "OUTCOME_SAFETY.csv").exists()
        )

    def test_invalid_output_is_rejected_as_dataframe_error(self):
        """A non-DataFrame result should raise a clear type error."""
        with self.assertRaisesRegex(TypeError, "DataFrame"):
            self.run_fake({
                "om": "out_df = 42",
            })

        self.assertFalse(
            (self.data_dir / "OUTCOME_MORTALITY.csv").exists()
        )

    def test_missing_provider_id_stays_missing(self):
        """Missing IDs must not become strings such as 000nan."""
        self.run_fake(
            {},
            provider_ids=["001234", None],
            values=[3.0, 4.0],
        )

        files = list(self.data_dir.glob("input_df*.csv"))
        self.assertEqual(len(files), 1)

        saved = pd.read_csv(
            files[0],
            dtype={"PROVIDER_ID": "string"},
        )

        self.assertEqual(saved.loc[0, "PROVIDER_ID"], "001234")
        self.assertTrue(
            pd.isna(saved.loc[1, "PROVIDER_ID"]),
            "Missing provider ID was converted into a string.",
        )

    def test_input_filename_is_correct(self):
        """The exported input file should use a predictable name."""
        self.run_fake({})

        self.assertTrue(
            (self.data_dir / "input_df.csv").exists(),
            "Expected input_df.csv, not input_df_{}.csv.",
        )

    def test_groups_cannot_modify_each_others_input(self):
        """Mortality's changes must not affect Safety's input."""
        self.run_fake({
            "om": (
                "input_df.loc[0, 'M1'] = 999\n"
                "out_df = input_df.copy()"
            ),
            "os": "out_df = input_df.copy()",
        })

        safety = pd.read_csv(
            self.data_dir / "OUTCOME_SAFETY.csv"
        )

        self.assertEqual(
            safety.loc[0, "M1"],
            3.0,
            "Safety received input modified by Mortality.",
        )

    def test_valid_dataframe_output_still_works(self):
        """Normal translation results should continue to export."""
        self.run_fake({
            "om": "out_df = input_df.copy()",
        })

        path = self.data_dir / "OUTCOME_MORTALITY.csv"

        self.assertTrue(path.exists())

        saved = pd.read_csv(
            path,
            dtype={"PROVIDER_ID": "string"},
        )

        self.assertEqual(saved.loc[0, "PROVIDER_ID"], "001234")
        self.assertEqual(saved.loc[0, "M1"], 3.0)

    def test_returns_success_status(self):
        result = self.run_fake({
            "om": "out_df = input_df.copy()",
        })
        self.assertIs(result, True)

    def test_returns_failure_status(self):
        result = self.run_fake({
            "om": "pass",
        })
        self.assertIs(result, False)

    def test_failed_step_removes_stale_output(self):
        self.run_fake(
            {"om": "pass"},
            stale_outputs={
                "OUTCOME_MORTALITY": pd.DataFrame({
                    "PROVIDER_ID": ["999999"],
                    "M1": [99.0],
                }),
            },
        )

        self.assertFalse(
            (self.data_dir / "OUTCOME_MORTALITY.csv").exists(),
            "Failed step left an old CSV that could be validated.",
        )


if __name__ == "__main__":
    unittest.main()
