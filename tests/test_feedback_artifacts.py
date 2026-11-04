"""Feedback-loop artifacts are ignored without hiding source or fixtures."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("git"), "Git is required to check ignore rules")
class FeedbackArtifactTests(unittest.TestCase):
    def test_only_generated_feedback_output_tree_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True,
                           capture_output=True)
            (root / ".gitignore").write_text(
                (ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8",
            )
            generated = [
                "output/program1_feedback/attempt_1/code/grp_score_macro.py",
                "output/program1_feedback/attempt_2/data/OUTCOME_SAFETY.csv",
                "output/program1_feedback/custom_run/report.json",
            ]
            legitimate = [
                "src/program1_integration.py",
                "tests/fixtures/program1_feedback/expected.csv",
                "tests/test_feedback_artifacts.py",
                "manifests/program1.json",
                "output/program1_feedback_fixtures/expected.csv",
            ]
            result = subprocess.run(
                ["git", "-c", "core.excludesFile=", "check-ignore", "--no-index", "-z", "--stdin"],
                cwd=root, input=("\0".join(generated + legitimate) + "\0").encode("utf-8"),
                capture_output=True, check=True,
            )
            ignored = result.stdout.decode("utf-8").rstrip("\0").split("\0")
            self.assertEqual(set(ignored), set(generated))
