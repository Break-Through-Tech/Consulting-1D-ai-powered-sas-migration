"""Run the reviewed Program 1 snapshot on the local Linux Docker daemon."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Timer
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "issue43-program1:local"
MAX_RESPONSE = 64 * 1024 * 1024
MODULES = ("program1_integration", "orchestrate", "validate", "pipeline_retry", "translation_history")


class DockerValidator:
    modules = MODULES
    check = "--check"

    def __init__(self):
        # A fresh environment also prevents forwarding Windows variables via WSLENV.
        self.env = {k: os.environ[k] for k in ("SystemRoot", "TEMP", "TMP") if k in os.environ}
        self.wsl = []
        if os.name == "nt":
            self.wsl = [str(Path(self.env["SystemRoot"]) / "System32/wsl.exe"),
                        "-d", "Ubuntu", "--cd", "/", "--exec"]
        self.command = [*self.wsl, "/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
                        "docker", "--config", "/var/empty/issue43-docker",
                        "--host", "unix:///var/run/docker.sock"]
        image = json.loads(self._docker("image", "inspect", IMAGE))[0]
        self.image = image["Id"]
        if (not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image)
                or image["Os"] != "linux" or image["Config"].get("Volumes")):
            raise ValueError("Expected a local Linux validator image without declared volumes")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._stage_worker(root)
            if self._invoke(root, self.check).strip() != b"ready":
                raise RuntimeError("Docker validator preflight failed")

    def _docker(self, *args):
        return subprocess.check_output([*self.command, *args], env=self.env, timeout=30)

    def _stage_worker(self, root):
        root.chmod(0o755)
        (root / "src").mkdir()
        for module in self.modules:
            shutil.copyfile(ROOT / "src" / f"{module}.py", root / "src" / f"{module}.py")
        (root / "src/__init__.py").touch()
        shutil.copyfile(ROOT / "docker/program1_worker.py", root / "worker.py")

    def _invoke(self, root, *args):
        mount = str(root.resolve())
        if self.wsl:
            mount = subprocess.check_output([*self.wsl, "/usr/bin/wslpath", "-a", mount],
                                            env=self.env, timeout=15).decode().strip()
        if not mount.startswith("/") or any(ch in mount for ch in ',\n\r"'):
            raise ValueError("Unsupported Docker staging path")
        name = "issue43-" + uuid4().hex
        self._docker("create", "--name", name, "--pull=never", "--network=none",
                     "--read-only", "--user=65534:65534", "--cap-drop=ALL",
                     "--security-opt=no-new-privileges", "--no-healthcheck", "--pids-limit=96", "--cpus=2",
                     "--memory=1g", "--memory-swap=1g", "--log-driver=none",
                     "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
                     "--mount", f"type=bind,src={mount},dst=/inputs,readonly",
                     "--entrypoint=/usr/bin/env", self.image, "-i", "LANG=C.UTF-8",
                     "OPENBLAS_NUM_THREADS=1", "/usr/local/bin/python", "-I", "-B",
                     "/inputs/worker.py", *args)
        try:
            mounts = json.loads(self._docker("inspect", "--format", "{{json .Mounts}}", name))
            binds = [m for m in mounts if m["Type"] != "tmpfs" or m["Destination"] != "/tmp"]
            if (len(binds) != 1 or binds[0]["Type"] != "bind" or binds[0]["RW"]
                    or binds[0]["Source"] != mount or binds[0]["Destination"] != "/inputs"):
                raise RuntimeError("Unexpected Docker mounts")
            with subprocess.Popen([*self.command, "start", "-a", name], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
                deadline = Timer(180, proc.kill)
                deadline.start()
                try:
                    response = proc.stdout.read(MAX_RESPONSE + 1)
                    if len(response) > MAX_RESPONSE:
                        raise ValueError("Docker validation response exceeded 64 MiB")
                    if proc.wait() != 0:
                        raise RuntimeError("Docker validator failed or timed out")
                finally:
                    proc.kill()
                    deadline.cancel()
            if self._docker("inspect", "--format", "{{.State.ExitCode}}", name).strip() != b"0":
                raise RuntimeError("Validation container exited unsuccessfully")
            return response
        finally:
            self._docker("rm", "-f", name)

    def __call__(self, snapshot, plan, input_csv, manifest, config, references, output_dir):
        tables = {step["group_config"]["output_table"] for step in plan if step["kind"] == "group"}
        if not all(re.fullmatch(r"[A-Za-z0-9_]+", key) for key in (*snapshot, *tables)):
            raise ValueError("Invalid Program 1 step or table name")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._stage_worker(root)
            (root / "code").mkdir()
            for step, source in snapshot.items():
                (root / "code" / f"{step}.py").write_bytes(source.encode("utf-8"))
            shutil.copyfile(input_csv, root / "input.csv")
            shutil.copyfile(manifest, root / "manifest.json")
            shutil.copyfile(config, root / "groups.json")
            (root / "references").mkdir()
            for table in tables:
                filename = f"{table}.csv"
                shutil.copyfile(Path(references) / filename, root / "references" / filename)
            payload = json.loads(self._invoke(root))
        report = payload["report"]
        if ([(s["id"], s["kind"]) for s in report["steps"]] != [(s["id"], s["kind"]) for s in plan]
                or set(report["outputs"]) != tables or not payload["tables"].keys() <= tables):
            raise ValueError("Container returned an unexpected validation plan")
        for step in report["steps"]:
            status = step["status"]
            if status in ("COMPLETE", "PASS", "FAIL") and step.get("generated_code") != snapshot[step["id"]]:
                raise ValueError("Container returned a different candidate")
        by_id = {s["id"]: s for s in report["steps"]}
        for group in (s for s in plan if s["kind"] == "group"):
            table = group["group_config"]["output_table"]
            step = by_id[group["id"]]
            out = report["outputs"][table]
            if out["step_id"] != group["id"] or out["status"] != step["status"]:
                raise ValueError("Container returned inconsistent step outcomes")
            if step["status"] in ("PASS", "FAIL") and (
                table not in payload["tables"] or out["validation"] != step["validation"]
                or out["validation"]["passed"] is not (step["status"] == "PASS")
            ):
                raise ValueError("Container returned incomplete validation evidence")
        passed = all(out["status"] == "PASS" for out in report["outputs"].values())
        if report["passed"] is not passed or report["status"] != ("PASS" if passed else "FAIL"):
            raise ValueError("Inconsistent Docker validation status")
        for table, csv in payload["tables"].items():
            (output_dir / f"{table}.csv").write_bytes(csv.encode("utf-8"))
        return report
