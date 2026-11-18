"""Step runner for run_step_plan: translate and run Program 0 and 2 steps (#61).

run_step_plan() walks a manifest in order and calls
execute_step(step, macro_vars, datasets) for each step. StepRunner is that
callback. For every step it asks the translator for Python, shows the code to a
reviewer, runs it in a prepared scope, and hands back the datasets and macro
variables the step produced.

Macro definitions are translated once into Python functions that are kept for
later steps. Steps that call a macro get those functions in their scope.

Generated code runs with exec(), so nothing runs until review_code approves it.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Callable, List

import numpy as np
import pandas as pd

from src import sas_procs
from src.orchestrate import run_step_plan
from src.pipeline_retry import retry_call


class StepExecutionError(RuntimeError):
    """A translated step could not be reviewed, run or did not return its results."""


def macro_name(step):
    """Name of the macro a `macro` step defines, e.g. 'kmeans' for %macro kmeans(...)."""
    match = re.search(r"%macro\s+(\w+)", step.get("sas_code", ""), re.IGNORECASE)
    if not match:
        raise StepExecutionError(f"{step['id']}: no %macro definition found in the SAS code.")
    return match.group(1).lower()


class StepRunner:
    """Callable for run_step_plan(plan, runner, ...).

    translator: needs translate_macro_definition(step, name) and
        translate_step(step, macro_vars, macros), as GeminiSASTranslator has.
    review_code: review_code(step, code) -> True to allow the code to run.
        There is no default, so generated code cannot run unreviewed.
    code_dir: when given, each step's reviewed code is saved as <step id>.py.
    """

    def __init__(
        self,
        translator,
        review_code: Callable[[dict, str], bool],
        *,
        code_dir=None,
        max_attempts=3,
        retry_delay_seconds=2.0,
    ):
        if not callable(review_code):
            raise TypeError("review_code must be a function (step, code) -> bool.")
        self.translator = translator
        self.review_code = review_code
        self.code_dir = Path(code_dir) if code_dir else None
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.macros = {}
        self.generated_code = {}

    def __call__(self, step, macro_vars, datasets):
        return self.execute_step(step, macro_vars, datasets)

    def execute_step(self, step, macro_vars, datasets):
        if step["kind"] == "macro":
            return self._define_macro(step)
        return self._run_step(step, macro_vars, datasets)

    # -- internals -----------------------------------------------------------

    def _request(self, operation):
        # Temporary API failures are retried; other errors are raised as they are.
        code, _attempts = retry_call(
            operation, max_attempts=self.max_attempts, delay_seconds=self.retry_delay_seconds,
        )
        return code

    def _scope(self):
        # Fresh scope per step, so one step cannot see another step's variables.
        return {"pd": pd, "np": np, "List": List, "sas_procs": sas_procs, **self.macros}

    def _approve(self, step, code):
        if not isinstance(code, str) or not code.strip():
            raise StepExecutionError(f"{step['id']}: the translator returned no Python code.")
        if self.review_code(step, code) is not True:
            raise StepExecutionError(f"{step['id']}: generated code was not approved.")
        self.generated_code[step["id"]] = code
        if self.code_dir is not None:
            self.code_dir.mkdir(parents=True, exist_ok=True)
            (self.code_dir / f"{step['id']}.py").write_bytes(code.encode("utf-8"))

    def _exec(self, step, code, scope):
        try:
            exec(code, scope)
        except Exception as error:
            raise StepExecutionError(
                f"{step['id']}: generated code failed: {type(error).__name__}: {error}"
            ) from error

    def _define_macro(self, step):
        name = macro_name(step)
        code = self._request(lambda: self.translator.translate_macro_definition(step, name))
        self._approve(step, code)

        scope = self._scope()
        self._exec(step, code, scope)
        function = scope.get(name)
        if not callable(function):
            raise StepExecutionError(
                f"{step['id']}: generated code must define a function named {name!r}."
            )
        self.macros[name] = function
        return {}

    def _run_step(self, step, macro_vars, datasets):
        signatures = {name: f"{name}{inspect.signature(fn)}" for name, fn in self.macros.items()}
        code = self._request(
            lambda: self.translator.translate_step(step, dict(macro_vars), signatures)
        )
        self._approve(step, code)

        scope = self._scope()
        # Copies, so a step cannot change a dataset that later steps still need.
        scope["datasets"] = {name: datasets[name].copy(deep=True) for name in step.get("inputs", [])}
        scope["macro_vars"] = dict(macro_vars)
        self._exec(step, code, scope)

        outputs = scope.get("outputs")
        if not isinstance(outputs, dict):
            raise StepExecutionError(f"{step['id']}: generated code must assign a dict named 'outputs'.")
        missing = [name for name in step.get("outputs", []) if name not in outputs]
        if missing:
            raise StepExecutionError(f"{step['id']}: outputs is missing {missing}.")
        not_frames = [name for name in step.get("outputs", [])
                      if not isinstance(outputs[name], pd.DataFrame)]
        if not_frames:
            raise StepExecutionError(f"{step['id']}: outputs {not_frames} are not DataFrames.")

        new_macro_vars = scope.get("new_macro_vars", {})
        if not isinstance(new_macro_vars, dict):
            raise StepExecutionError(f"{step['id']}: 'new_macro_vars' must be a dict.")

        # Only the declared outputs leave the step.
        return {
            "macro_vars": dict(new_macro_vars),
            "datasets": {name: outputs[name] for name in step.get("outputs", [])},
        }


# -- Generate, review, then run (#73) ---------------------------------------
#
# Same flow as Program 1: generate_step_code() saves one file per step without
# running anything, a person reads the files, and run_saved_steps() runs those
# exact files. A later run reuses the files and makes no Gemini calls.


def _signature(name, code):
    """Signature of the function `name` in macro code, read without running it."""
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return f"{name}({ast.unparse(node.args)})"
    return None


def generate_step_code(
    translator,
    plan,
    code_dir,
    *,
    max_attempts=3,
    retry_delay_seconds=2.0,
):
    """Translate every step of a plan and save <step id>.py files. Runs no generated code.

    Returns {step id: path}. The directory must be new or empty, so versions
    are never mixed.
    """
    code_dir = Path(code_dir)
    if code_dir.exists() and any(code_dir.iterdir()):
        raise FileExistsError(
            f"Code directory is not empty: {code_dir}. Use a new directory."
        )
    code_dir.mkdir(parents=True, exist_ok=True)

    signatures = {}
    files = {}
    for step in plan:
        if step.get("action") != "translate":
            continue

        if step["kind"] == "macro":
            name = macro_name(step)
            operation = lambda step=step, name=name: translator.translate_macro_definition(step, name)
        else:
            # Runtime macro values are not known yet. The prompt tells the AI to read
            # them from macro_vars, so the placeholder text stays in the request.
            operation = lambda step=step: translator.translate_step(
                step, dict(step.get("macro_vars", {})), dict(signatures)
            )

        code, _attempts = retry_call(
            operation, max_attempts=max_attempts, delay_seconds=retry_delay_seconds,
        )
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"{step['id']}: the translator returned no Python code.")
        try:
            compile(code, f"{step['id']}.py", "exec")  # syntax check only, nothing runs
        except SyntaxError as error:
            raise ValueError(f"{step['id']}: generated code is not valid Python: {error}") from error

        if step["kind"] == "macro":
            signature = _signature(name, code)
            if signature is None:
                raise ValueError(f"{step['id']}: generated code must define a function named {name!r}.")
            signatures[name] = signature

        path = code_dir / f"{step['id']}.py"
        path.write_bytes(code.encode("utf-8"))
        files[step["id"]] = path

    return files


class SavedStepCode:
    """Translator stand-in that reads saved files, so no Gemini call is made."""

    def __init__(self, code_dir):
        self.code_dir = Path(code_dir)
        if not self.code_dir.is_dir():
            raise FileNotFoundError(f"Code directory not found: {self.code_dir}")

    def check(self, plan):
        """Raise before anything runs if any translated step has no saved file."""
        missing = [
            step["id"] for step in plan
            if step.get("action") == "translate" and not (self.code_dir / f"{step['id']}.py").is_file()
        ]
        if missing:
            raise FileNotFoundError(f"Missing saved code for steps: {missing}")

    def _read(self, step):
        return (self.code_dir / f"{step['id']}.py").read_bytes().decode("utf-8")

    def translate_macro_definition(self, step, macro_name):
        return self._read(step)

    def translate_step(self, step, macro_vars, macros):
        return self._read(step)


def run_saved_steps(
    plan,
    code_dir,
    *,
    reviewed,
    initial_macro_vars=None,
    initial_datasets=None,
):
    """Run a plan from saved code files. `reviewed` must be True: read the files first.

    Generated code runs with full access to this computer, as in Program 1.
    """
    if reviewed is not True:
        raise StepExecutionError(
            "Saved code has not been marked as reviewed. Read the files in "
            f"{code_dir} first, then pass reviewed=True."
        )
    saved = SavedStepCode(code_dir)
    saved.check(plan)
    runner = StepRunner(saved, lambda step, code: True, max_attempts=1)
    return run_step_plan(
        plan, runner,
        initial_macro_vars=initial_macro_vars, initial_datasets=initial_datasets,
    )
