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
import time
from pathlib import Path
from typing import Callable, List
from uuid import uuid4

import numpy as np
import pandas as pd

from src import sas_procs
from src.orchestrate import run_step_plan
from src.pipeline_retry import RequestNotDispatched, retry_call
from src.translation_history import CandidateHistory, HistoryStorageError, error_details


class StepExecutionError(RuntimeError):
    """A translated step could not be reviewed, run or did not return its results."""


class StepSetupError(RuntimeError):
    """The runner cannot prepare this step for execution."""


def macro_name(step):
    """Name of the macro a `macro` step defines, e.g. 'kmeans' for %macro kmeans(...)."""
    match = re.search(r"%macro\s+(\w+)", step.get("sas_code", ""), re.IGNORECASE)
    if not match:
        raise StepSetupError(f"{step['id']}: no %macro definition found in the SAS code.")
    return match.group(1).lower()


def expand_name_list(text, columns=None):
    """Split a SAS variable list into names and expand ranges: 'A HAI_1-HAI_3' -> A HAI_1 HAI_2 HAI_3.

    A range is NAME<number>-NAME<number> (or NAME<number>-<number>) with the same
    NAME on both sides, counting up. Generated code gets this in its scope because
    every step that reads &measure_all needs it, and it is easy to get wrong.

    With columns (for example df.columns), each name is returned spelled as in
    columns, ignoring case, because SAS does: &measure_all says OP_18b and the data
    column is OP_18B. A name with no matching column is returned as written.
    """
    names = _expand_ranges(text)
    if columns is None:
        return names

    spelling = {}
    for column in columns:
        if isinstance(column, str):
            spelling.setdefault(column.upper(), column)
    return [spelling.get(name.upper(), name) for name in names]


def _expand_ranges(text):
    names = []
    for token in str(text).split():
        if "-" not in token:
            names.append(token)
            continue
        left, right = token.split("-", 1)
        start = re.fullmatch(r"(.*?)(\d+)", left)
        end = re.fullmatch(r"(.*?)(\d+)", right)
        if not start or not end or end.group(1) not in ("", start.group(1)):
            raise ValueError(f"Cannot expand the SAS name range {token!r}.")
        first, last = int(start.group(2)), int(end.group(2))
        if last < first:
            raise ValueError(f"Cannot expand the SAS name range {token!r}: it counts down.")
        names.extend(f"{start.group(1)}{number}" for number in range(first, last + 1))
    return names


def _match_column(columns, key):
    """The column that `key` means, ignoring case, or `key` itself.

    Only a text name that is not an exact column, and that matches exactly one column
    ignoring case, is changed. Anything else is left for pandas to handle.
    """
    if isinstance(key, str) and key not in columns:
        matches = [c for c in columns if isinstance(c, str) and c.upper() == key.upper()]
        if len(matches) == 1:
            return matches[0]
    return key


def _match_columns(columns, keys):
    """_match_column for one name or a list of names. Other objects (masks, functions) pass through."""
    if isinstance(keys, (list, tuple)):
        return type(keys)(_match_column(columns, key) for key in keys)
    return _match_column(columns, keys)


class SasFrame(pd.DataFrame):
    """A DataFrame whose column names ignore case, like SAS (df['Measure_Name'] finds measure_name).

    Works for df[name], df[[names]], df[name] = value, sort_values(by=), drop(columns=),
    groupby(by), dropna(subset=), drop_duplicates(subset=) and merge(on=, left_on=, right_on=).
    A name is changed only when the exact name is missing and exactly one column matches
    ignoring case. Generated code reads columns under a different case than the one that
    made them, and SAS accepts that.

    Not covered: `'x' in df.columns` (use expand_name_list for name lists), `pd.merge(a, b, on=)`
    as a function, and the column picked after a groupby (`df.groupby('g')['V']`).
    """

    @property
    def _constructor(self):
        return SasFrame

    def _match(self, key):
        return _match_columns(self.columns, key)

    def __getitem__(self, key):
        return super().__getitem__(self._match(key))

    def __setitem__(self, key, value):
        super().__setitem__(self._match(key), value)

    def sort_values(self, by=None, *args, **kwargs):
        if kwargs.get("axis", 0) in (0, "index"):
            by = self._match(by)
        return super().sort_values(by, *args, **kwargs)

    def drop(self, labels=None, *args, **kwargs):
        if kwargs.get("axis", 0) in (1, "columns"):
            labels = self._match(labels)
        if "columns" in kwargs:
            kwargs["columns"] = self._match(kwargs["columns"])
        return super().drop(labels, *args, **kwargs)

    def groupby(self, by=None, *args, **kwargs):
        return super().groupby(self._match(by), *args, **kwargs)

    def dropna(self, *args, **kwargs):
        if "subset" in kwargs:
            kwargs["subset"] = self._match(kwargs["subset"])
        return super().dropna(*args, **kwargs)

    def drop_duplicates(self, *args, **kwargs):
        if "subset" in kwargs:
            kwargs["subset"] = self._match(kwargs["subset"])
        return super().drop_duplicates(*args, **kwargs)

    def merge(self, right, *args, **kwargs):
        # SAS MERGE ... BY x matches x in both data sets whatever their case. When the
        # two spellings differ, the right data set's column takes the left one's spelling.
        if isinstance(right, pd.DataFrame):
            if kwargs.get("on") is not None:
                keys = kwargs["on"] if isinstance(kwargs["on"], (list, tuple)) else [kwargs["on"]]
                renames = {}
                for key in keys:
                    left_name = _match_column(self.columns, key)
                    right_name = _match_column(right.columns, key)
                    if left_name != right_name and right_name in right.columns:
                        renames[right_name] = left_name
                if renames:
                    right = right.rename(columns=renames)
                kwargs["on"] = _match_columns(self.columns, kwargs["on"])
            if kwargs.get("left_on") is not None:
                kwargs["left_on"] = self._match(kwargs["left_on"])
            if kwargs.get("right_on") is not None:
                kwargs["right_on"] = _match_columns(right.columns, kwargs["right_on"])
        return super().merge(right, *args, **kwargs)


class SasNames(dict):
    """A dict whose keys ignore case, like SAS dataset names (WORK.X is work.x).

    Generated code often builds a dataset name from a macro variable such as
    quarter ('Jul'), which gives a different case than the manifest ('JUL').
    """

    def __init__(self, items=()):
        super().__init__()
        self.update(items)

    @staticmethod
    def _key(key):
        return key.upper() if isinstance(key, str) else key

    def __getitem__(self, key):
        return super().__getitem__(self._key(key))

    def __setitem__(self, key, value):
        super().__setitem__(self._key(key), value)

    def __contains__(self, key):
        return super().__contains__(self._key(key))

    def __delitem__(self, key):
        super().__delitem__(self._key(key))

    def get(self, key, default=None):
        return super().get(self._key(key), default)

    def pop(self, key, *default):
        return super().pop(self._key(key), *default)

    def setdefault(self, key, default=None):
        return super().setdefault(self._key(key), default)

    def update(self, items=(), **named):
        for key, value in dict(items, **named).items():
            self[key] = value


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
        return {"pd": pd, "np": np, "List": List, "sas_procs": sas_procs,
                "expand_name_list": expand_name_list, **self.macros}

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
        for name in step.get("inputs", []):
            if not isinstance(datasets.get(name), pd.DataFrame):
                raise StepSetupError(f"{step['id']}: required input {name!r} is missing or is not a DataFrame.")
        signatures = {name: f"{name}{inspect.signature(fn)}" for name, fn in self.macros.items()}
        code = self._request(
            lambda: self.translator.translate_step(step, dict(macro_vars), signatures)
        )
        self._approve(step, code)

        scope = self._scope()
        # Copies, so a step cannot change a dataset that later steps still need.
        scope["datasets"] = SasNames(
            {name: SasFrame(datasets[name].copy(deep=True)) for name in step.get("inputs", [])}
        )
        scope["macro_vars"] = dict(macro_vars)
        self._exec(step, code, scope)

        outputs = scope.get("outputs")
        if not isinstance(outputs, dict):
            raise StepExecutionError(f"{step['id']}: generated code must assign a dict named 'outputs'.")
        outputs = SasNames(outputs)
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
            # Plain DataFrames, so the case-insensitive lookups stay inside the step.
            "datasets": {name: pd.DataFrame(outputs[name]) for name in step.get("outputs", [])},
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


def _code_problem(code):
    """Why saved code would be rejected, or None. Compiles only; nothing runs."""
    if not isinstance(code, str) or not code.strip():
        return "the translator returned no Python code."
    try:
        compile(code, "step.py", "exec")
    except SyntaxError as error:
        return f"generated code is not valid Python: {error}"
    return None


def request_step_candidate(translator, step, signatures, history, *, feedback=None, execution_id=None,
                           previous_candidate_id=None, macro_candidates=None, check_request_budget=None,
                           max_attempts=3, retry_delay_seconds=2.0):
    """Record one candidate request. Transport retries do not create extra candidates."""
    step_id = step["id"]
    request_id = None
    repair = {}
    if feedback is not None:
        prior = history.pointer["candidates"].get(step_id)
        if previous_candidate_id is None and prior:
            previous_candidate_id = prior["candidate_id"]
        repair = {
            "type": "repair_request", "step_ids": [step_id], "feedback": feedback,
            "previous_candidate_id": previous_candidate_id,
        }
        if execution_id is not None:
            repair["execution_id"] = execution_id
        if macro_candidates:
            repair["macro_candidates"] = macro_candidates
    if check_request_budget is not None:
        try:
            check_request_budget()
        except RequestNotDispatched as error:
            history.record({**repair, "type": "request_rejected", "step_id": step_id,
                            "request_number": 1, "error": error_details(error)})
            raise
    if repair:
        request_id = history.record(repair)
    requests = 0

    def request():
        nonlocal requests
        requests += 1
        extra = {"feedback": feedback} if feedback is not None else {}
        try:
            if step["kind"] == "macro":
                return translator.translate_macro_definition(step, macro_name(step), **extra)
            return translator.translate_step(step, dict(step.get("macro_vars", {})), dict(signatures), **extra)
        except Exception as error:
            try:
                kind = "request_rejected" if isinstance(error, RequestNotDispatched) else "request_error"
                history.record({"type": kind, "step_id": step_id,
                                "repair_request_id": request_id, "request_number": requests,
                                "error": error_details(error)})
            except Exception as storage_error:
                raise HistoryStorageError(str(storage_error)) from storage_error
            raise

    code, _ = retry_call(request, max_attempts=max_attempts, delay_seconds=retry_delay_seconds)
    candidate_id = None
    if isinstance(code, str):
        # A repair after a non-string response is not a first-generation success.
        origin = "generated" if request_id is None else "repair"
        candidate_id = history.candidate(
            step, code, origin=origin, previous_candidate_id=previous_candidate_id,
            repair_request_id=request_id, select=execution_id is None,
        )
    problem = _code_problem(code)
    if not problem and step["kind"] == "macro" and _signature(macro_name(step), code) is None:
        problem = f"generated code must define a function named {macro_name(step)!r}."
    if problem:
        history.record({"type": "generation_error", "step_id": step_id,
                        "candidate_id": candidate_id, "repair_request_id": request_id,
                        "error": {"type": "ValueError", "message": problem}})
    return code, problem, candidate_id


def generate_step_code(
    translator,
    plan,
    code_dir,
    *,
    max_attempts=3,
    retry_delay_seconds=2.0,
    min_interval_seconds=0.0,
    max_syntax_attempts=3,
    program=None,
    check_request_budget=None,
):
    """Translate every step of a plan and save <step id>.py files. Runs no generated code.

    Returns {step id: path}. The directory must be new or empty, so versions
    are never mixed. min_interval_seconds waits between requests, for API plans
    with a per-minute limit (the Gemini free tier allows 15 requests a minute).
    """
    if type(max_syntax_attempts) is not int or not 1 <= max_syntax_attempts <= 3:
        raise ValueError("max_syntax_attempts must be an integer between 1 and 3.")
    if min_interval_seconds < 0:
        raise ValueError("min_interval_seconds cannot be negative.")
    code_dir = Path(code_dir)
    if code_dir.exists() and any(code_dir.iterdir()):
        raise FileExistsError(
            f"Code directory is not empty: {code_dir}. Use a new directory."
        )
    code_dir.mkdir(parents=True, exist_ok=True)
    metadata = {"repair_history": True}
    if program is not None:
        metadata["program"] = program
    if isinstance(getattr(translator, "model_name", None), str):
        metadata["model_name"] = translator.model_name
    history = CandidateHistory(code_dir, metadata=metadata, new=True)

    signatures = {}
    files = {}
    first_request = True
    for step in plan:
        if step.get("action") != "translate":
            continue

        if min_interval_seconds and not first_request:
            time.sleep(min_interval_seconds)
        first_request = False

        name = macro_name(step) if step["kind"] == "macro" else None

        feedback = None
        for syntax_attempt in range(1, max_syntax_attempts + 1):
            code, problem, _ = request_step_candidate(
                translator, step, signatures, history, feedback=feedback,
                max_attempts=max_attempts, retry_delay_seconds=retry_delay_seconds,
                check_request_budget=check_request_budget,
            )
            if problem is None:
                break
            # A syntactically valid answer missing its macro has always stopped generation.
            if syntax_attempt == max_syntax_attempts or (name and _code_problem(code) is None):
                raise ValueError(f"{step['id']}: {problem}")
            # At temperature 0 the same request gives the same answer, so say what was wrong.
            feedback = f"Your previous answer was rejected: {problem}"

        if step["kind"] == "macro":
            signatures[name] = _signature(name, code)

        path = code_dir / f"{step['id']}.py"
        path.write_bytes(code.encode("utf-8"))
        files[step["id"]] = path

    return files


class SavedStepCode:
    """Translator stand-in that reads saved files, so no Gemini call is made."""

    def __init__(self, code_dir):
        self.code_dir = Path(code_dir)
        self.snapshot = {}
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
        if step["id"] in self.snapshot:
            return self.snapshot[step["id"]]
        return (self.code_dir / f"{step['id']}.py").read_bytes().decode("utf-8")

    def freeze(self, plan):
        self.check(plan)
        self.snapshot = {step["id"]: self._read(step) for step in plan if step["action"] == "translate"}

    def translate_macro_definition(self, step, macro_name):
        return self._read(step)

    def translate_step(self, step, macro_vars, macros):
        return self._read(step)


def _start_execution(plan, saved, history):
    execution_id = uuid4().hex
    candidates = {
        step["id"]: history.candidate(step, saved.snapshot[step["id"]], origin="reviewed_file", reuse=True)
        for step in plan if step["action"] == "translate"
    }
    history.record({"type": "execution_started", "execution_id": execution_id,
                    "candidates": candidates, "execution_plan": plan,
                    "review_status": "caller_supplied_reviewed_code"})
    return execution_id, candidates


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
    saved.freeze(plan)
    runner = StepRunner(saved, lambda step, code: True, max_attempts=1)
    inputs = dict(initial_macro_vars=initial_macro_vars, initial_datasets=initial_datasets)
    if not (Path(code_dir) / "translation_history.json").exists():
        return run_step_plan(plan, runner, **inputs)

    history = CandidateHistory(code_dir)
    execution_id, candidates = _start_execution(plan, saved, history)
    active = None
    failure_status = "failed"

    def execute(step, macro_vars, datasets):
        nonlocal active, failure_status
        active = step
        try:
            return runner(step, macro_vars, datasets)
        except Exception as error:
            failure_status = "failed" if isinstance(error, StepExecutionError) else "blocked"
            raise

    def outcome(step, status="ok", error=None):
        nonlocal active
        active = None
        history.record({"type": "outcome", "execution_id": execution_id,
                        "candidate_id": candidates[step["id"]], "validation_status": "not_run",
                        "result": {"id": step["id"], "kind": step["kind"], "status": status, "error": error}})

    try:
        return run_step_plan(plan, execute, on_step_complete=outcome, **inputs)
    except Exception as error:
        if active is not None:
            outcome(active, failure_status, str(error))
        history.record({"type": "execution_error", "execution_id": execution_id, "error": error_details(error)})
        raise


def run_steps_report(
    plan,
    code_dir,
    *,
    reviewed,
    initial_macro_vars=None,
    initial_datasets=None,
):
    """Run saved code like run_saved_steps, but never stop at a failing step.

    run_step_plan raises at the first failure. This keeps going and returns what
    happened to every step, so a person (or a repair loop, #66) can see which step
    failed and why, and which later steps could not run because of it.

    Returns a dict:
      steps       one entry per step, in plan order:
                  {"id", "kind", "status", "error", "blocked_by"}
                  status is "ok", "skipped" (a context step), "failed" or "blocked".
                  error is the message of a failed or blocked step, else None.
                  blocked_by lists the steps (or macro variables) that stopped it.
      counts      how many steps have each status.
      datasets    every dataset produced, plus the ones passed in.
      macro_vars  every macro variable resolved, plus the ones passed in.

    `reviewed` must be True: read the files first. Generated code runs with full
    access to this computer.
    """
    if reviewed is not True:
        raise StepExecutionError(
            "Saved code has not been marked as reviewed. Read the files in "
            f"{code_dir} first, then pass reviewed=True."
        )
    saved = SavedStepCode(code_dir)
    saved.freeze(plan)
    history = CandidateHistory(code_dir) if (Path(code_dir) / "translation_history.json").exists() else None
    return _report_snapshot(plan, saved, history, initial_macro_vars, initial_datasets)


def _report_snapshot(plan, saved, history, initial_macro_vars, initial_datasets):
    candidates = {}
    if history:
        execution_id, candidates = _start_execution(plan, saved, history)
    runner = StepRunner(saved, lambda step, code: True, max_attempts=1)

    macro_vars = dict(initial_macro_vars or {})
    datasets = dict(initial_datasets or {})
    steps = []
    status_of = {}

    for step in plan:
        entry = {"id": step["id"], "kind": step["kind"], "status": None, "error": None, "blocked_by": []}
        steps.append(entry)

        # Resolve macro variables the way run_step_plan does.
        must_set, unresolved = [], []
        for name, value in step.get("macro_vars", {}).items():
            if isinstance(value, str):
                description = value.strip().lower()
                if description == "macro parameter":
                    continue
                if description.startswith("runtime ("):
                    if "set in this step" in description:
                        must_set.append(name)
                    elif name not in macro_vars:
                        unresolved.append(name)
                    continue
            macro_vars[name] = value

        if step.get("action") == "skip":
            entry["status"] = "skipped"
        else:
            blocked_by = [
                dependency for dependency in step.get("depends_on", [])
                if status_of.get(dependency) not in ("ok", "skipped")
            ]
            if blocked_by or unresolved:
                entry["status"] = "blocked"
                entry["blocked_by"] = blocked_by + [f"macro variable {name}" for name in unresolved]
                entry["error"] = "waiting for " + ", ".join(entry["blocked_by"])
            else:
                try:
                    result = runner.execute_step(step, dict(macro_vars), dict(datasets))
                    returned = result.get("macro_vars", {})
                    not_set = [name for name in must_set if name not in returned]
                    if not_set:
                        raise StepExecutionError(f"{step['id']}: runtime macro variables not set: {not_set}.")
                    datasets.update(result.get("datasets", {}))
                    macro_vars.update(returned)
                    entry["status"] = "ok"
                except StepExecutionError as error:
                    entry["status"] = "failed"
                    entry["error"] = f"{type(error).__name__}: {error}"
                except Exception as error:
                    entry["status"] = "blocked"
                    entry["error"] = f"{type(error).__name__}: {error}"
                    entry["blocked_by"] = ["step setup"]
        status_of[step["id"]] = entry["status"]
        if step["id"] in candidates:
            history.record({"type": "outcome", "candidate_id": candidates[step["id"]],
                            "execution_id": execution_id, "result": entry, "validation_status": "not_run"})

    counts = {status: sum(entry["status"] == status for entry in steps)
              for status in ("ok", "skipped", "failed", "blocked")}
    report = {"steps": steps, "counts": counts, "datasets": datasets, "macro_vars": macro_vars}
    if history:
        report["execution_id"] = execution_id
    return report
