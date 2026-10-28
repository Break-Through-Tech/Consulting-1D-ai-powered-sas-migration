"""Golden test: the parser's Program 1 output vs the hand-written manifests/program1.json.

The hand-written manifest is the baseline (it's what Amanda's translator and
Lizzette's pipeline use today). The parser must reproduce its structure.
Fields a person wrote by hand (titles, notes, explanations) are not compared.

Expected differences, on purpose:
  * ctx_libs        - the hand-written manifest copies one %LET line from
                      Program 0; the parser only makes steps from the file it reads.
  * group inputs    - the parser also lists the WORK table each group's PROC SQL
                      counts (e.g. WORK.OUTCOMES_MORTALITY, made by Program 0).
                      SAS really reads it; the hand-written file left it out
                      because the translator uses the config instead.
  * macro inputs    - the parser writes '<indsn macro parameter>' (what the macro
                      reads depends on the call); the hand-written file wrote the
                      table every call happens to pass.
  * macro_vars values - hand-written pointers like "config/... -> OM.measures";
                      the parser says "runtime (set by ...)". The keys must match.
"""

import json
import unittest
from pathlib import Path

from src.sas_reader.__main__ import PROGRAMS
from src.sas_reader.manifest import MACROS_FILE, parse_program

ROOT = Path(__file__).resolve().parent.parent
EXACT_FIELDS = ["kind", "translate", "depends_on", "source_file", "line_start", "line_end",
                "sas_code", "outputs", "macros_called"]


class Program1Golden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hand = json.loads((ROOT / "manifests" / "program1.json").read_text(encoding="utf-8"))
        cls.gen = parse_program(PROGRAMS["1"], MACROS_FILE, [PROGRAMS["0"]])
        cls.by_id = {s["id"]: s for s in cls.gen}

    def test_same_steps_in_same_order(self):
        hand_ids = [s["id"] for s in self.hand if s["id"] != "ctx_libs"]
        self.assertEqual([s["id"] for s in self.gen], hand_ids)

    def test_structure_matches(self):
        for h in self.hand:
            if h["id"] == "ctx_libs":
                continue
            g = self.by_id[h["id"]]
            for f in EXACT_FIELDS:
                with self.subTest(step=h["id"], field=f):
                    self.assertEqual(g[f], h[f])

    def test_macro_var_names_match(self):
        for h in self.hand:
            if h["id"] != "ctx_libs":
                with self.subTest(step=h["id"]):
                    self.assertEqual(set(self.by_id[h["id"]]["macro_vars"]), set(h["macro_vars"]))

    def test_inputs_differ_only_as_documented(self):
        for h in self.hand:
            g = self.by_id.get(h["id"])
            if g is None or g["kind"] == "macro":
                continue
            extra = set(g["inputs"]) - set(h["inputs"])
            with self.subTest(step=h["id"]):
                self.assertTrue(set(h["inputs"]) <= set(g["inputs"]))
                self.assertTrue(all(x.startswith("WORK.") for x in extra), extra)

    def test_everything_supported(self):
        self.assertEqual([s["id"] for s in self.gen if s["status"] != "supported"], [])


if __name__ == "__main__":
    unittest.main()
