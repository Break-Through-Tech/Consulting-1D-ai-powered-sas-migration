"""Setup steps in the README work without a real API key."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import program1_integration, translator


class FakeTranslator:
    def translate_macro(self, step):
        return "def grp_score(df, varlist):\n    return df.copy()\n"

    def translate_group_step(self, step, measures):
        return "out_df = grp_score(input_df, measures)\n"


class ApiKeyTests(unittest.TestCase):
    def test_key_is_loaded_from_env_file(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / ".env").write_text("GEMINI_API_KEY=key-from-file\n")

            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(translator, "ROOT", Path(temp)), \
                    patch.object(translator.genai, "Client") as client:
                translator.GeminiSASTranslator()

        client.assert_called_once_with(api_key="key-from-file")

    def test_exported_key_wins_over_env_file(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / ".env").write_text("GEMINI_API_KEY=key-from-file\n")

            with patch.dict(os.environ, {"GEMINI_API_KEY": "exported-key"}, clear=True), \
                    patch.object(translator, "ROOT", Path(temp)), \
                    patch.object(translator.genai, "Client") as client:
                translator.GeminiSASTranslator()

        client.assert_called_once_with(api_key="exported-key")

    def test_env_example_names_the_key_the_translator_reads(self):
        root = Path(translator.__file__).resolve().parent.parent
        self.assertIn("GEMINI_API_KEY=", (root / ".env.example").read_text())


class GenerateCommandTests(unittest.TestCase):
    def test_command_writes_code_without_running_it(self):
        with tempfile.TemporaryDirectory() as temp:
            code_dir = Path(temp) / "code"
            output = io.StringIO()

            with patch.object(translator, "GeminiSASTranslator", FakeTranslator), \
                    patch.object(program1_integration, "run_program1") as run, \
                    contextlib.redirect_stdout(output):
                program1_integration.main(["--code-dir", str(code_dir)])

            self.assertEqual(
                {path.name for path in code_dir.iterdir()},
                {"grp_score_macro.py", "om.py", "os.py", "or.py", "ptexp.py", "process.py",
                 ".translation_history", "translation_history.json"},
            )
            run.assert_not_called()
            self.assertIn("Read the code", output.getvalue())


if __name__ == "__main__":
    unittest.main()
