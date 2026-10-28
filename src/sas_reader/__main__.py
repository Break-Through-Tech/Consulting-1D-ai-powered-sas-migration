"""Command line: python -m src.sas_reader <program> --out <manifest.json>

<program> is 0, 1 or 2 (our SAS programs) or a path to a .sas file.
Earlier programs are read automatically for the macro variables they set,
because SAS runs them in one session (Program 2 uses values from 0 and 1).

Examples:
    python -m src.sas_reader 0 --out manifests/program0.json
    python -m src.sas_reader 2 --out manifests/program2.json
    python -m src.sas_reader 1            # print to the screen instead of a file
"""

import argparse
import json
from pathlib import Path

from .manifest import MACROS_FILE, SAS_DIR, parse_program

PROGRAMS = {n: next(SAS_DIR.glob(f"{n} - *.sas")) for n in ("0", "1", "2")}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Split a SAS program into translator-ready steps.")
    ap.add_argument("program", help="0, 1, 2 or a path to a .sas file")
    ap.add_argument("--out", help="where to write the manifest JSON (default: print it)")
    args = ap.parse_args(argv)

    if args.program in PROGRAMS:
        path = PROGRAMS[args.program]
        globals_paths = [PROGRAMS[n] for n in PROGRAMS if n < args.program]
    else:
        path, globals_paths = Path(args.program), []

    steps = parse_program(path, MACROS_FILE, globals_paths)
    text = json.dumps(steps, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        bad = [s["id"] for s in steps if s["status"] != "supported"]
        print(f"Wrote {len(steps)} steps to {args.out}" + (f"; UNSUPPORTED: {bad}" if bad else ""))
    else:
        print(text)


if __name__ == "__main__":
    main()
