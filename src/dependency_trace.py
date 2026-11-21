"""Trace possible sources and downstream effects of incorrect outputs.

Uses the existing step plan. A dependency trace identifies candidates,
not a confirmed cause of a numerical mismatch.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def _key(value):
    return str(value).strip().casefold()


def _dataset_name(value):
    value = str(value).strip()
    return bool(value) and not value.startswith("<")


def _build_graph(plan):
    steps = [
        step for step in plan
        if isinstance(step, dict) and step.get("id")
    ]

    ids = [step["id"] for step in steps]

    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate step IDs in plan")

    parents = {step_id: set() for step_id in ids}
    children = {step_id: set() for step_id in ids}

    last_writer = {}
    writers = defaultdict(list)

    for step in steps:
        step_id = step["id"]

        # Explicit dependencies from build_step_plan.
        for dependency in step.get("depends_on", []) or []:
            if dependency in parents and dependency != step_id:
                parents[step_id].add(dependency)

        # Include the latest earlier step that wrote each input.
        for name in step.get("inputs", []) or []:
            if not _dataset_name(name):
                continue

            previous_writer = last_writer.get(_key(name))

            if previous_writer and previous_writer != step_id:
                parents[step_id].add(previous_writer)

        # A dataset may be written more than once.
        for name in step.get("outputs", []) or []:
            if not _dataset_name(name):
                continue

            normalized = _key(name)
            writers[normalized].append(step_id)
            last_writer[normalized] = step_id

    for step_id, dependencies in parents.items():
        for dependency in dependencies:
            children[dependency].add(step_id)

    return steps, parents, children, writers


def _reachable(start, edges):
    visited = set()
    remaining = list(start)

    while remaining:
        current = remaining.pop()

        if current in visited:
            continue

        visited.add(current)
        remaining.extend(edges.get(current, ()))

    return visited


def trace_output(plan, output_name):
    """Find possible contributing steps for a named output."""

    steps, parents, children, writers = _build_graph(plan)
    order = {
        step["id"]: position
        for position, step in enumerate(steps)
    }

    matching_writers = writers.get(_key(output_name), [])

    if not matching_writers:
        raise ValueError(
            f"No step produces output {output_name!r}"
        )

    # The final writer produces the saved version of the output.
    direct_writer = matching_writers[-1]

    # Work backward to find contributing steps.
    candidates = _reachable([direct_writer], parents)

    earliest = {
        step_id for step_id in candidates
        if not (parents[step_id] & candidates)
    }

    # Steps downstream of the actual final output writer.
    downstream = (
        _reachable([direct_writer], children) - {direct_writer}
    )

    def output_names(step_ids):
        return sorted({
            name
            for step in steps
            if step["id"] in step_ids
            for name in step.get("outputs", []) or []
            if _dataset_name(name)
        })

    # Begin investigating at the output writer and its direct parents.
    priority = [direct_writer] + sorted(
        parents[direct_writer],
        key=order.get,
        reverse=True,
    )
    priority = list(dict.fromkeys(priority))

    # An earlier candidate may affect additional branches.
    candidate_impacts = {}

    for candidate in priority:
        later = _reachable([candidate], children) - {candidate}

        candidate_impacts[candidate] = {
            "downstream_steps": sorted(later, key=order.get),
            "downstream_outputs": output_names(later),
        }

    return {
        "output": output_name,
        "direct_writer": direct_writer,
        "earlier_writers": matching_writers[:-1],
        "investigate_first": list(dict.fromkeys(priority)),
        "earliest_upstream_candidates": sorted(
            earliest, key=order.get
        ),
        "all_candidate_steps": sorted(
            candidates, key=order.get, reverse=True
        ),
        "downstream_steps": sorted(downstream, key=order.get),
        "downstream_outputs": output_names(downstream),
        "candidate_downstream_impact": candidate_impacts,
        "note": (
            "These are possible dependency paths. "
            "The actual cause is not confirmed."
        ),
    }


def trace_validation_report(report, plans):
    """Trace failures and identify their earliest observed pipeline stage."""

    result = {
        "failed_outputs": {},
        "unsupported": {},
        "earliest_failing_stage": None,
        "earliest_failing_outputs": [],
        "later_stage_failures": [],
    }

    failures_by_stage = {}

    for name, entry in report.get("outputs", {}).items():
        if str(entry.get("status", "")).upper() != "FAIL":
            continue

        try:
            program = int(entry.get("program"))
        except (TypeError, ValueError):
            result["unsupported"][name] = "Unknown program"
            continue

        if program not in (0, 1, 2):
            result["unsupported"][name] = (
                f"Unsupported program: {program}"
            )
            continue

        failures_by_stage.setdefault(program, []).append(name)

        if program not in plans:
            result["unsupported"][name] = (
                f"Program {program} plan is unavailable"
            )
            continue

        try:
            result["failed_outputs"][name] = {
                "program": program,
                **trace_output(plans[program], name),
            }
        except ValueError as error:
            result["unsupported"][name] = str(error)

    if failures_by_stage:
        earliest = min(failures_by_stage)

        result["earliest_failing_stage"] = earliest
        result["earliest_failing_outputs"] = (
            failures_by_stage[earliest]
        )

        for program in sorted(failures_by_stage):
            if program <= earliest:
                continue

            for name in failures_by_stage[program]:
                result["later_stage_failures"].append({
                    "program": program,
                    "output": name,
                })

    result["stage_order_note"] = (
        "The earliest failing stage is the first observed "
        "discrepancy among validated outputs. Later failures "
        "may be consequences, but causality is not confirmed."
    )

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Trace possible steps behind failing outputs"
    )

    parser.add_argument("validation_report", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional path to save a JSON trace",
    )

    args = parser.parse_args()

    from src.orchestrate import build_step_plan, build_program1_plan

    root = Path(__file__).resolve().parent.parent

    plans = {
        program: build_step_plan(
            root / f"manifests/program{program}.json"
        )
        for program in (0, 2)
    }
    plans[1] = build_program1_plan()

    report = json.loads(
        args.validation_report.read_text(encoding="utf-8")
    )

    result = trace_validation_report(report, plans)
    rendered = json.dumps(result, indent=2)

    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"Saved trace to {args.out}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
