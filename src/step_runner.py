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

import inspect
import re
from pathlib import Path
from typing import Callable, List

import numpy as np
import pandas as pd

from src import sas_procs


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
    ):
        if not callable(review_code):
            raise TypeError("review_code must be a function (step, code) -> bool.")
        self.translator = translator
        self.review_code = review_code
        self.code_dir = Path(code_dir) if code_dir else None
        self.macros = {}
        self.generated_code = {}

    def __call__(self, step, macro_vars, datasets):
        return self.execute_step(step, macro_vars, datasets)

    def execute_step(self, step, macro_vars, datasets):
        if step["kind"] == "macro":
            return self._define_macro(step)
        return self._run_step(step, macro_vars, datasets)

    # -- internals -----------------------------------------------------------

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
        code = self.translator.translate_macro_definition(step, name)
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
        code = self.translator.translate_step(step, dict(macro_vars), signatures)
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
