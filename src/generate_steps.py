"""Get Python code for Program 0 or 2 from Gemini and save it for review (#61, #73).

    python -m src.generate_steps 0
    python -m src.generate_steps 2 --code-dir output/program2_generated_code

This only generates and saves code. It does not run it. Read the files in the code
directory first. They run with full access to this computer.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.orchestrate import build_step_plan
from src.step_runner import generate_step_code

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None, translator=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("program", choices=["0", "2"], help="SAS program to translate")
    parser.add_argument("--code-dir", help="where to save the code (must be new or empty)")
    parser.add_argument(
        "--min-interval", type=float, default=0.0, metavar="SECONDS",
        help="wait between requests; use 5 on the Gemini free tier (15 requests a minute)",
    )
    args = parser.parse_args(argv)

    code_dir = Path(args.code_dir) if args.code_dir else ROOT / "output" / f"program{args.program}_generated_code"
    plan = build_step_plan(ROOT / "manifests" / f"program{args.program}.json")

    if translator is None:
        # Imported here so --help and tests do not need a Gemini key.
        from src.translator import GeminiSASTranslator
        translator = GeminiSASTranslator()

    files = generate_step_code(translator, plan, code_dir, min_interval_seconds=args.min_interval)
    print(f"Saved code for {len(files)} steps in {code_dir}")
    print("Read it before running it. It runs with full access to this computer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
