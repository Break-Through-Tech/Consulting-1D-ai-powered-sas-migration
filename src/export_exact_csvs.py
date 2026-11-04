"""Convert the provided .sas7bdat files to full-precision CSVs.

The CSV exports we were given are rounded. These files hold the same values as
the .sas7bdat files, with the same file names as the originals.

Command line: python -m src.export_exact_csvs <output_dir>
"""

import argparse
from pathlib import Path

from . import data_io


def export_exact_csvs(output_dir):
    """Write the SAS input and every SAS output as CSV. Returns the paths written."""
    output_dir = Path(output_dir)

    tables = {f"{data_io.INPUT_DATASET}.csv": data_io.load_input()}
    for name in data_io.OUTPUT_DATASETS:
        tables[f"{name.upper()}.csv"] = data_io.load_sas_output(name)

    # Check everything first so a blocked run writes nothing.
    existing = [name for name in tables if (output_dir / name).exists()]
    if existing:
        raise FileExistsError(
            f"Refusing to overwrite {existing} in {output_dir}. "
            "Use a new directory or move those files first."
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for name, table in tables.items():
        path = output_dir / name
        table.to_csv(path, index=False)
        written.append(path)

    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert the .sas7bdat files to full-precision CSVs.")
    parser.add_argument("output_dir", help="where to write the CSVs; existing files are never overwritten")
    args = parser.parse_args(argv)

    written = export_exact_csvs(args.output_dir)
    print(f"Wrote {len(written)} CSV files to {args.output_dir}")


if __name__ == "__main__":
    main()
