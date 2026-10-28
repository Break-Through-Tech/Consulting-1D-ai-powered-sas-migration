"""SAS Reader: split our SAS programs into translator-ready steps (issue #36).

Use from Python:
    from src.sas_reader import parse_program
    steps = parse_program("data/Project_1/SAS Programs/0 - Data and Measure Standardization_2025Jul.sas")

Use from the command line (writes a manifest JSON):
    python -m src.sas_reader 0 --out manifests/program0.json

See docs/DOCUMENTATION_sas_reader.md for the format and how teammates use it.
"""

from .manifest import parse_program  # noqa: F401
