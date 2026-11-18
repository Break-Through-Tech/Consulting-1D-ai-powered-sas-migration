"""One report on how all 10 Python outputs compare with SAS (#38).

Command line, from the main project folder:
    python -m src.validation_report --baseline     the hand-written Python (no API key)
    python -m src.validation_report output/data    a folder of <OUTPUT NAME>.csv files

It prints a readable report and saves it, with the same results as JSON, in
output/validation_report/. It never runs generated code.

src/validate.py does every comparison. This module only decides which key each
output uses (data_io.OUTPUTS), gathers the results and writes them as sentences.
The JSON fields are described in docs/validation_report.md.
"""

import argparse
import json
import math
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src import data_io
from src.baseline import program0, program1, program2
from src.validate import DEFAULT_ATOL, DEFAULT_RTOL, compare_detailed, read_keyed_csv

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_DIR = ROOT / "output" / "validation_report"
JSON_NAME = "validation_report.json"
TEXT_NAME = "validation_report.txt"

# Raise this only when a field is renamed or removed. Adding a field does not change it.
SCHEMA_VERSION = 1

STATUSES = ("PASS", "FAIL", "ERROR", "NOT_CHECKED")
BASELINE_SOURCE = "the hand-written Python in src/baseline (not AI output)"

# The readable report cuts long lists of names short, and shows a row for only the
# first few columns that differ. The JSON always keeps everything.
SHOWN_NAMES = 5
SHOWN_COLUMNS = 5


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------
def compare_output(name, table):
    """Compare one Python output with its SAS table, using the key in data_io.OUTPUTS.

    name is a SAS output name in upper or lower case. table is a DataFrame, or the
    Path of a CSV file. Returns what validate.compare_detailed returns.
    """
    return _compare(name, table)[0]


def _compare(name, table):
    """What compare_output returns, and the names of the SAS columns that hold text."""
    facts = data_io.OUTPUTS.get(str(name).lower())
    if facts is None:
        raise ValueError(f"{name!r} is not one of the SAS outputs: {', '.join(data_io.OUTPUTS)}")

    if isinstance(table, Path):
        table = _read_csv(table, facts["key"])
    if not isinstance(table, pd.DataFrame):
        raise TypeError(f"Expected a pandas DataFrame or the Path of a CSV file, got {type(table).__name__}.")
    _reject_repeated_columns(table)

    sas = data_io.load_sas_output(name)
    # Spelled the way the validator reports column names. Every other SAS column holds numbers.
    sas_text_columns = [
        str(column).strip().upper()
        for column in sas.columns
        if not pd.api.types.is_numeric_dtype(sas[column])
    ]
    return compare_detailed(table, sas, facts["key"]), sas_text_columns


def _read_csv(path, key):
    """Read an output CSV: the key stays text, and only an empty cell is a missing value."""
    try:
        # to_csv writes a missing value as an empty cell. Without these options, read_csv
        # also reads the words nan, None, NA and NULL as missing values, so a text value
        # made by str() of a missing value would equal a SAS blank and pass.
        return read_keyed_csv(path, key, keep_default_na=False, na_values=[""])
    except pd.errors.EmptyDataError:
        # pd.DataFrame().to_csv(index=False) writes a file with no header. Treat it as the
        # empty table it came from, so the validator can say which columns are missing.
        return pd.DataFrame()


def _reject_repeated_columns(table):
    """SAS ignores case in column names, so Mean and mean are one column there."""
    spellings = {}
    for column in table.columns:
        spellings.setdefault(str(column).strip().upper(), []).append(str(column))

    # In quotes, so that a name which differs only by a space at its end can be seen.
    repeated = [" and ".join(repr(name) for name in names) for names in spellings.values() if len(names) > 1]
    if repeated:
        raise ValueError("The Python table has columns that SAS would treat as one: " + "; ".join(repeated))


def build_report(tables, source, programs=None):
    """Compare every SAS output that was given, and return the report as a dict.

    tables maps a SAS output name (upper or lower case) to a DataFrame or to the Path
    of a CSV file. Other names, such as WORK tables, are ignored, so the datasets
    that run_step_plan returns can be passed as they are. An output with no table is
    NOT_CHECKED, and the report is then INCOMPLETE, never PASS.

    source is a short label saying where the tables came from. It is shown at the top
    of the report, so a hand-written result is never mistaken for an AI result.

    programs lists the programs this run produced (default: all three). One number is
    enough for one program. Outputs of other programs are NOT_CHECKED even when a
    table is present. Use it for a run of one program that was given SAS tables as
    inputs: run_step_plan returns those too, and they would match themselves.

    The dict holds only JSON values and is exactly what the JSON file contains.
    """
    if not isinstance(tables, Mapping):
        # A DataFrame has .items() too, and would read as "no table was given".
        raise TypeError(
            "tables must be a dict of {output name: DataFrame or CSV Path}, "
            f"got {type(tables).__name__}. For one table use compare_output(name, table)."
        )
    programs = _check_programs(programs)

    given = {}
    for name, table in tables.items():
        lowered = str(name).lower()
        if lowered not in data_io.OUTPUTS:
            continue
        if lowered in given:
            raise ValueError(f"Two tables are named {lowered} (names ignore case).")
        given[lowered] = table

    outputs = {}
    for name, facts in data_io.OUTPUTS.items():
        # The key is spelled the way the validator reports column names.
        entry = {
            "program": facts["program"],
            "key": facts["key"].upper() if facts["key"] else None,
            "status": "NOT_CHECKED",
        }

        if name in given and facts["program"] in programs:
            try:
                validation, sas_text_columns = _compare(name, given[name])
                validation = _json_safe(validation)
            except Exception as error:
                # One table that cannot be compared must not hide the other nine.
                entry["status"] = "ERROR"
                entry["error"] = {"type": type(error).__name__, "message": str(error)}
            else:
                entry["status"] = "PASS" if validation["passed"] else "FAIL"
                entry["largest_difference"] = _largest_difference(validation)
                entry["sas_text_columns"] = sas_text_columns
                entry["validation"] = validation

        outputs[name.upper()] = entry

    counts = {
        status: sum(entry["status"] == status for entry in outputs.values())
        for status in STATUSES
    }
    if counts["FAIL"] or counts["ERROR"]:
        status = "FAIL"
    elif counts["NOT_CHECKED"]:
        status = "INCOMPLETE"
    else:
        status = "PASS"

    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(source),
        "programs": programs,
        "rtol": DEFAULT_RTOL,
        "atol": DEFAULT_ATOL,
        "status": status,
        "passed": status == "PASS",
        "counts": counts,
        "outputs": outputs,
    }


def _check_programs(programs):
    """The programs to report on, as a sorted list of 0, 1 and 2."""
    known = sorted({facts["program"] for facts in data_io.OUTPUTS.values()})
    if programs is None:
        return known

    try:
        programs = list(programs)
    except TypeError:
        programs = [programs]  # one number is enough for one program
    # True equals 1 in Python, so it would pass for Program 1.
    if not programs or any(isinstance(program, (bool, np.bool_)) or program not in known for program in programs):
        raise ValueError(f"programs must be one or more of {known}, got {programs!r}.")
    return sorted({int(program) for program in programs})


def _largest_difference(validation):
    """The biggest Python-minus-SAS gap in any column, or None if no numbers were compared."""
    gaps = [
        stats["max_abs_diff"]
        for stats in validation["column_stats"].values()
        if stats["max_abs_diff"] is not None
    ]
    return max(gaps) if gaps else None


def _json_safe(value):
    """Copy a validation result so it holds only values that strict JSON can hold.

    A missing value becomes None. JSON has no infinity, so it becomes "inf" or "-inf".
    """
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return value
    if isinstance(value, (bool, int, str)):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Where the Python tables come from
# ---------------------------------------------------------------------------
def baseline_tables():
    """The 10 outputs of the hand-written Python in src/baseline, run on the SAS input."""
    program0_result = program0.run(data_io.load_input())
    group_outputs = program1.run(program0_result.std_data_analysis, program0_result.group_measures)

    return {
        **program0.outputs(program0_result),
        **group_outputs,
        **program2.run(group_outputs),
    }


def csv_tables(folder):
    """Find the output CSVs in a folder: {OUTPUT NAME: path} for each <OUTPUT NAME>.csv.

    File names ignore case, and other files are ignored. The files are read later,
    inside build_report, so one unreadable file is one ERROR and not a crash.
    """
    found = {}
    for path in sorted(Path(folder).iterdir()):
        if path.is_file() and path.suffix.lower() == ".csv" and path.stem.lower() in data_io.OUTPUTS:
            name = path.stem.upper()
            if name in found:
                # Possible where file names tell upper from lower case, as on Linux.
                raise ValueError(f"Two files hold {name}: {found[name].name} and {path.name}. Keep one.")
            found[name] = path
    return found


def _csv_source(folder, paths):
    """Label for a folder of CSVs. The save times make a file left from an older run visible."""
    if not paths:
        return f"CSV files in {folder} (no file named like STAR_2025JUL.csv found)"

    saved = sorted(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) for path in paths)
    return f"CSV files in {folder} (saved {_minute(saved[0])} to {_minute(saved[-1])} UTC)"


def _minute(moment):
    return f"{moment:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# The readable report
# ---------------------------------------------------------------------------
LEGEND = [
    "HOW TO READ THIS",
    "  PASS         Same rows, same columns in the same order, and every value equal within the tolerance.",
    "  FAIL         The tables were compared and something differs. The details are listed above.",
    "  ERROR        The Python table could not be read or compared. The reason is listed above.",
    "  NOT CHECKED  This output was not compared: no Python table was given for it, or its program",
    "               was left out of this report.",
    f"  Tolerance: two numbers are equal when they differ by no more than {DEFAULT_ATOL:g} plus "
    f"{DEFAULT_RTOL:g} times",
    "  the SAS number (ignoring its sign). That is about 9 matching digits.",
    "  Rows are matched by the column shown, so their order does not matter. An output matched by",
    "  row position has no such column, so its rows are compared in order. Upper and lower case",
    "  in column names is ignored, as in SAS.",
    "  Largest difference is the biggest gap between a Python number and the SAS number in the same",
    "  cell, leaving out cells where either one is missing or infinite. 1e-13 means 0.0000000000001.",
    "  First difference is the first row that differs, in the order of the Python table.",
    "  If an output fails, look at the rows. Do not raise the tolerance.",
    f"  Every column, with up to 5 example rows each, is in {JSON_NAME}.",
]


def format_report(report):
    """The report as plain text for people. A saved JSON file gives the same text."""
    outputs = report["outputs"]
    not_passing = [name for name, entry in outputs.items() if entry["status"] in ("FAIL", "ERROR")]
    created = datetime.fromisoformat(report["created_at"]).astimezone(timezone.utc)

    lines = [
        "SAS vs Python validation report",
        f"Checked:   {report['source']}",
        f"Created:   {_minute(created)} UTC",
        f"Tolerance: relative {report['rtol']:g}, absolute {report['atol']:g}",
        "",
        result_line(report),
    ]
    if len(not_passing) > 1:
        lines.extend([
            f"Start with {not_passing[0]}. Outputs are listed in pipeline order, and each program reads",
            "the outputs of the one before, so an early difference can cause the later ones.",
        ])

    lines.append("")
    lines.extend(_summary_table(outputs))

    for name, entry in outputs.items():
        if entry["status"] == "PASS" and entry["validation"]["sas_rows"] == 0:
            lines.extend(["", f"Note: {name} has no rows in SAS, so only its columns were checked."])

    if not_passing:
        lines.extend(["", "WHAT DIFFERS"])
        for name in not_passing:
            entry = outputs[name]
            lines.extend(["", f"{name} (Program {entry['program']}): {entry['status']}"])
            lines.extend("  " + line for line in problem_lines(entry))

    lines.extend(["", *LEGEND])
    return "\n".join(lines) + "\n"


def result_line(report):
    """The verdict in one sentence."""
    counts = report["counts"]
    parts = [f"{counts['PASS']} of {len(report['outputs'])} outputs match SAS"]
    if counts["FAIL"]:
        parts.append(f"{counts['FAIL']} {'differs' if counts['FAIL'] == 1 else 'differ'}")
    if counts["ERROR"]:
        parts.append(f"{counts['ERROR']} could not be compared")
    if counts["NOT_CHECKED"]:
        parts.append(f"{counts['NOT_CHECKED']} not checked")

    return f"RESULT: {report['status']} - " + ", ".join(parts)


def _summary_table(outputs):
    rows = [("Program", "Output", "Result", "Rows Python / SAS", "Rows matched by", "Largest difference")]
    for name, entry in outputs.items():
        validation = entry.get("validation")
        if entry["status"] == "PASS":
            # Shown for a pass only. For a failure it could hide the real problem,
            # because it leaves out missing values; WHAT DIFFERS explains those.
            gap = entry["largest_difference"]
            closeness = "-" if gap is None else _number(gap)
        else:
            closeness = "-" if entry["status"] == "NOT_CHECKED" else "see below"

        rows.append((
            str(entry["program"]),
            name,
            entry["status"].replace("_", " "),
            f"{validation['python_rows']:,} / {validation['sas_rows']:,}" if validation else "-",
            entry["key"] or "row position",
            closeness,
        ))

    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    table = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]
    table.insert(1, "  ".join("-" * width for width in widths))
    return table


def problem_lines(entry):
    """Plain sentences saying why one output is not PASS. Empty for PASS and NOT_CHECKED.

    entry is one entry of build_report(...)["outputs"], or the same entry read back from
    the JSON file.
    """
    if entry["status"] == "ERROR":
        # pandas can end a message with a line break. Here it goes on one line.
        reason = " ".join(str(entry["error"]["message"]).split())
        return [
            "The table could not be read or compared, so it is not known whether it matches SAS.",
            f"Reason: {entry['error']['type']}: {reason}",
        ]
    if entry["status"] != "FAIL":
        return []

    result = entry["validation"]
    key = entry["key"]
    lines = []

    if result["python_rows"] != result["sas_rows"]:
        lines.append(f"Row count differs: Python has {result['python_rows']:,}, SAS has {result['sas_rows']:,}.")
    if result["missing_columns"]:
        lines.append(f"{_plural(len(result['missing_columns']), 'SAS column')} missing in Python: "
                     f"{_some(result['missing_columns'])}")
    if result["extra_columns"]:
        lines.append(f"{_plural(len(result['extra_columns']), 'Python column')} not in SAS: "
                     f"{_some(result['extra_columns'])}")
        if any(name.startswith("UNNAMED:") for name in result["extra_columns"]):
            lines.append("UNNAMED means a column with no name, usually the row numbers pandas adds. "
                         "Save the table with to_csv(index=False).")
    if not result["column_order_match"] and not result["missing_columns"] and not result["extra_columns"]:
        position, python_name, sas_name = next(
            (number, python_name, sas_name)
            for number, (python_name, sas_name)
            in enumerate(zip(result["python_columns"], result["sas_columns"]), start=1)
            if python_name != sas_name
        )
        lines.append("Same columns, but in a different order. "
                     f"The first difference is column {position}: {python_name} in Python, {sas_name} in SAS.")

    if not result["key_found"]:
        lines.append(f"Rows are matched by {key}, and the Python table has no {key} column, "
                     "so no values were compared.")
        return lines

    # The SAS keys are unique and never blank (a test checks every output), so only
    # Python's get a sentence.
    blank, repeated = result["blank_python_keys"], result["duplicate_python_ids"]
    if blank:
        lines.append(f"{_plural(blank, f'blank {key} value')} in Python.")
    if repeated:
        lines.append(f"{_plural(len(repeated), f'{key} value')} repeated in Python: {_some(repeated)}")

    missing, extra = result["missing_ids"], result["extra_ids"]
    if missing:
        lines.append(f"{_plural(len(missing), 'row')} in SAS but not in Python: {_row_names(missing, key)}")
    if extra:
        lines.append(f"{_plural(len(extra), 'row')} in Python but not in SAS: {_row_names(extra, key)}")
    if key and {value.lstrip("0") for value in missing} & set(extra):
        lines.append(f"Some {key} values in Python are the SAS values without their leading zeros. "
                     f"Keep {key} as text.")

    if blank or repeated or result["blank_sas_keys"] or result["duplicate_sas_ids"]:
        lines.append("Rows could not be matched one to one, so no values were compared.")
        return lines

    mismatches = result["mismatches"]
    if mismatches:
        # Only a column compared as numbers has statistics. A column without them that
        # holds numbers in SAS is not a number column in Python: it is text, or numbers
        # kept as Python objects, which np.where(..., None) makes. A row that is missing
        # on one side differs whatever the type is, so the column must also have a row
        # with a value on both sides.
        as_text = {
            column for column, mismatch in mismatches.items()
            if column not in result["column_stats"] and column not in entry["sas_text_columns"]
            and mismatch["count"] > mismatch["python_missing"] + mismatch["sas_missing"]
        }

        lines.append(f"Values differ in {_plural(len(mismatches), 'column')}:")
        # Every column gets a line. SAS puts the results last (GRP_SCORE, STAR), so a list
        # cut short would leave out the very columns a reader looks for.
        for number, (column, mismatch) in enumerate(mismatches.items()):
            lines.extend(_column_lines(column, mismatch, result["column_stats"].get(column), key,
                                       show_row=number < SHOWN_COLUMNS, as_text=column in as_text))
        if len(mismatches) > SHOWN_COLUMNS:
            lines.append(f"A first difference is shown for the first {SHOWN_COLUMNS} columns only. "
                         f"The rest are in {JSON_NAME}.")
        if as_text:
            # Said once, because it is often true of many columns at the same time.
            lines.append("A column is compared as text when it is not a number column in Python. "
                         "SAS holds numbers there, and text never equals a number.")
    elif lines:
        # Only the shape differs. Say that the values were compared and are equal, so that
        # this cannot be taken for "the values were not looked at".
        rows = result["sas_rows"] - len(missing)
        columns = len(result["sas_columns"]) - len(result["missing_columns"])
        # The key column is in both tables too, but it holds no value to compare.
        if rows > 0 and columns - (key is not None) > 0:
            lines.append(f"In the {_plural(rows, 'row')} and {_plural(columns, 'column')} "
                         "that both tables have, every value is equal.")

    # Never show a bare FAIL, even for a check added to the validator later.
    return lines or [f"The comparison failed. See validation in {JSON_NAME}."]


def _column_lines(column, mismatch, stats, key, show_row, as_text):
    """One column that differs: how many rows, why, and (if show_row) the first of them."""
    python_field, sas_field = f"{column}_py", f"{column}_sas"
    # Only a column compared as numbers has statistics. Any other was compared as text.
    numeric = stats is not None
    count = mismatch["count"]
    line = f"- {column}: {_plural(count, 'row')} {'differs' if count == 1 else 'differ'}"

    one_sided = []
    if mismatch["python_missing"]:
        one_sided.append(f"{mismatch['python_missing']:,} where Python has no value and SAS has one")
    if mismatch["sas_missing"]:
        one_sided.append(f"{mismatch['sas_missing']:,} where SAS has no value and Python has one")
    line += f" ({'; '.join(one_sided)})." if one_sided else "."

    if as_text:
        line += " Compared as text."

    # The statistic skips missing values. At or below the absolute tolerance it can only
    # be float noise from rows that match, and would point away from the real difference.
    if numeric and stats["max_abs_diff"] is not None and stats["max_abs_diff"] > DEFAULT_ATOL:
        line += f" Largest difference {_number(stats['max_abs_diff'])}."

    lines = [line]
    if show_row and mismatch["examples"]:
        example = mismatch["examples"][0]
        # The remaining field identifies the row: the key, or the row position.
        row = next(value for field, value in example.items() if field not in (python_field, sas_field))
        lines.append(f"    First difference: {_row_names([row], key)}: "
                     f"Python {_value(example[python_field], numeric)}, "
                     f"SAS {_value(example[sas_field], numeric)}")
    return lines


def _plural(count, noun):
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def _some(names):
    """The first few names, then how many more there are."""
    shown = ", ".join(str(name) for name in names[:SHOWN_NAMES])
    more = len(names) - SHOWN_NAMES
    return f"{shown} and {more:,} more" if more > 0 else shown


def _row_names(ids, key):
    """Row ids for people: 'PROVIDER_ID 010001, 010005', or 'row 1' for a table with no key."""
    if key is None:
        # The validator counts positions from 0 and stores them as text.
        return _some([f"row {position + 1}" for position in sorted(int(value) for value in ids)])
    return f"{key} {_some(ids)}"


def _value(value, numeric):
    if value is None:
        return "(missing)"
    # The JSON writes infinity as "inf" or "-inf". In a text column those letters are text.
    if numeric and value in ("inf", "-inf"):
        return value.replace("inf", "infinity")
    return repr(value)


def _number(value):
    """Three significant digits. From 1,000 up, a whole number with commas, not 2.56e+03.

    A runaway value such as 1e+300 keeps the short form, or it would fill the screen.
    """
    return f"{value:,.0f}" if 999.5 <= value < 1e15 else f"{value:.3g}"


# ---------------------------------------------------------------------------
# Saving and the command line
# ---------------------------------------------------------------------------
def save_report(report, folder=None):
    """Write validation_report.json and validation_report.txt into folder. Returns both paths.

    The default folder is output/validation_report. An older report in the same
    folder is replaced. Pass another folder to keep it.
    """
    folder = Path(DEFAULT_REPORT_DIR if folder is None else folder)
    folder.mkdir(parents=True, exist_ok=True)

    json_path = folder / JSON_NAME
    text_path = folder / TEXT_NAME
    json_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    text_path.write_text(format_report(report), encoding="utf-8")
    return json_path, text_path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare the Python outputs with the SAS results and write one report.",
    )
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument(
        "outputs_dir", nargs="?",
        help="folder of Python outputs saved as CSV files named like STAR_2025JUL.csv, for example output/data",
    )
    where.add_argument(
        "--baseline", action="store_true",
        help="check the hand-written Python in src/baseline (no API key needed)",
    )
    parser.add_argument(
        "--out", default=DEFAULT_REPORT_DIR,
        help=f"folder for {JSON_NAME} and {TEXT_NAME} (default: output/validation_report)",
    )
    args = parser.parse_args(argv)
    if "" in (args.outputs_dir, args.out):
        # What a shell variable that is not set gives. It would mean the current folder.
        parser.error("a folder name is empty")

    try:
        if args.baseline:
            tables, source = baseline_tables(), BASELINE_SOURCE
        else:
            folder = Path(args.outputs_dir)
            if not folder.is_dir():
                parser.error(f"{'not a folder' if folder.exists() else 'folder not found'}: {folder}")
            # samefile, not a comparison of spellings: a Mac opens "sas output csv" as the same folder.
            if data_io.SAS_OUTPUT_CSV_DIR.is_dir() and folder.samefile(data_io.SAS_OUTPUT_CSV_DIR):
                parser.error("that folder holds the SAS results themselves, not Python outputs")
            tables = csv_tables(folder)
            source = _csv_source(folder, list(tables.values()))

        report = build_report(tables, source)
        json_path, text_path = save_report(report, args.out)
    except OSError as error:
        # A folder that cannot be read or written.
        parser.error(str(error))
    except Exception:
        # Exit code 1 says that the report was written, so nothing that stops the report
        # here may end with 1. The traceback shows where it stopped.
        traceback.print_exc()
        return 2

    print(format_report(report))
    print(f"Saved {json_path}")
    print(f"Saved {text_path}")
    # Repeated last, because the end of the output is what stays on screen. What was
    # checked comes with it, so a hand-written result is not taken for an AI result.
    print(f"Checked:   {report['source']}")
    print(result_line(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
