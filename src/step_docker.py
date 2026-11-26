"""Validate one approved snapshot using the existing restricted Docker launcher."""
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from src import validation_report
from src.program1_docker import DockerValidator
from src.run_program import PROGRAM_OUTPUTS
from src.step_runner import _start_execution
from src.step_worker import read_input
from src.translation_history import write_json_atomic


class StepDockerValidator(DockerValidator):
    modules = ("step_worker", "step_runner", "sas_procs", "orchestrate", "pipeline_retry",
               "translation_history")
    check = "--check-steps"

    def __init__(self, program, run_dir):
        self.outputs = PROGRAM_OUTPUTS[program]
        self.program = program
        self.run_dir = run_dir
        super().__init__()

    def __call__(self, plan, saved, history, macro_vars, datasets):
        execution, candidates = _start_execution(plan, saved, history)
        directory = self.run_dir / "executions" / execution
        directory.mkdir(parents=True)
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._stage_worker(root)
                (root / "code").mkdir()
                for name, code in saved.snapshot.items():
                    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                        raise ValueError("Invalid step filename")
                    (root / "code" / f"{name}.py").write_bytes(code.encode("utf-8"))
                inputs = []
                for i, (name, frame) in enumerate(datasets.items()):
                    path = root / f"input_{i}.csv"
                    dtypes = frame.dtypes.astype(str).to_dict()
                    frame.to_csv(path, index=False)
                    if not read_input(path, dtypes).equals(frame):
                        raise ValueError(f"Staging changed values or types in {name}")
                    inputs.append((name, dtypes))
                write_json_atomic(root / "job.json", {"plan": plan, "inputs": inputs,
                                  "macro_vars": macro_vars, "outputs": self.outputs})
                payload = json.loads(self._invoke(root, "--steps"))
            steps, tables = payload["steps"], payload["tables"]
            if ([(s["id"], s["kind"]) for s in steps] != [(s["id"], s["kind"]) for s in plan]
                    or not isinstance(tables, dict) or not tables.keys() <= set(self.outputs)):
                raise ValueError("Unexpected worker steps or output tables")
            for step, entry in zip(plan, steps):
                allowed = {"skipped"} if step["action"] == "skip" else {"ok", "failed", "blocked"}
                if (entry["status"] not in allowed or
                        entry["status"] == "failed" and not isinstance(entry["error"], str)):
                    raise ValueError("Invalid worker step outcome")
            for entry in steps:
                if entry["id"] in candidates:
                    history.record({"type": "outcome", "execution_id": execution,
                                    "candidate_id": candidates[entry["id"]], "result": entry,
                                    "validation_status": "not_run"})
            paths = {}
            for name, csv in tables.items():
                paths[name] = directory / f"{name}.csv"
                paths[name].write_bytes(csv.encode("utf-8"))
            report = validation_report.build_report(paths, "isolated step execution", [self.program])
            write_json_atomic(directory / "validation.json", report)
            counts = {s: sum(row["status"] == s for row in steps)
                      for s in ("ok", "skipped", "failed", "blocked")}
            outputs = {name: report["outputs"][name] for name in self.outputs}
            passed = (not counts["failed"] and not counts["blocked"]
                      and all(o["status"] == "PASS" for o in outputs.values()))
            status = "PASS" if passed else "FAIL"
            history.record({"type": "run_finished", "execution_id": execution, "status": status,
                            "step_statuses": {s["id"]: s["status"] for s in steps}, "outputs": outputs})
            return {"steps": steps, "counts": counts, "execution_id": execution, "status": status}
        except (Exception, KeyboardInterrupt) as error:
            history.record({"type": "execution_error", "execution_id": execution,
                            "error": {"type": type(error).__name__}})
            raise
