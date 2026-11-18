"""The generate command writes one file per translated step of the real manifests."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from src.generate_steps import main
from src.orchestrate import build_step_plan
from src.step_runner import macro_name

ROOT = Path(__file__).resolve().parent.parent


class Echo:
    """Stands in for Gemini: macros become a function, steps assign empty outputs."""

    def translate_macro_definition(self, step, name):
        return f"def {name}(*args, **kwargs):\n    return None\n"

    def translate_step(self, step, macro_vars, macros):
        return "outputs = {}"


class GenerateStepsTests(unittest.TestCase):
    def generate(self, program):
        with tempfile.TemporaryDirectory() as directory:
            code_dir = Path(directory) / "code"
            with contextlib.redirect_stdout(io.StringIO()):
                status = main([program, "--code-dir", str(code_dir)], translator=Echo())
            return status, sorted(path.stem for path in code_dir.glob("*.py"))

    def test_every_translated_step_gets_a_file(self):
        for program in ("0", "2"):
            with self.subTest(program=program):
                plan = build_step_plan(ROOT / "manifests" / f"program{program}.json")
                expected = sorted(step["id"] for step in plan if step["action"] == "translate")
                status, saved = self.generate(program)

                self.assertEqual(0, status)
                self.assertEqual(expected, saved)

    def test_macro_signatures_reach_the_call_steps_of_program_2(self):
        plan = build_step_plan(ROOT / "manifests" / "program2.json")
        self.assertIn("kmeans", {macro_name(step) for step in plan if step["kind"] == "macro"})


if __name__ == "__main__":
    unittest.main()
