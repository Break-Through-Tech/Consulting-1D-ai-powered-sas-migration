"""Entry point staged read-only alongside the trusted Program 1 sources."""
import os
from pathlib import Path
import shutil
import sys

INPUT = Path("/inputs")
SCRATCH = Path("/tmp/program1")


def check_isolation():
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    if (os.getuid() != 65534 or int(status["CapEff"], 16) or int(status["CapBnd"], 16)
            or int(status["NoNewPrivs"]) != 1 or int(status["Seccomp"]) != 2
            or set(os.listdir("/sys/class/net")) != {"lo"}
            or not all(os.statvfs(p).f_flag & os.ST_RDONLY for p in ("/", INPUT))):
        raise RuntimeError("Program 1 container isolation check failed")


def main():
    check_isolation()
    sys.path.insert(0, str(INPUT))
    from src.program1_integration import run_reviewed_program1
    from src.translation_history import write_json_atomic

    if sys.argv[1:] == ["--check"]:
        print("ready")
        return
    # Ordinary candidate prints must not corrupt the JSON transport.
    result_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    SCRATCH.mkdir()
    code = shutil.copytree(INPUT / "code", SCRATCH / "code")
    data = SCRATCH / "data"
    report = run_reviewed_program1(
        code_dir=code, input_csv=INPUT / "input.csv", output_dir=data,
        manifest_path=INPUT / "manifest.json", config_path=INPUT / "groups.json",
        sas_output_dir=INPUT / "references", on_error="report",
    )
    tables = {p.stem: p.read_text(encoding="utf-8") for p in data.glob("*.csv")}
    path = SCRATCH / "response.json"
    write_json_atomic(path, {"report": report, "tables": tables})
    with os.fdopen(result_fd, "wb") as stream:
        stream.write(path.read_bytes())


if __name__ == "__main__":
    main()
