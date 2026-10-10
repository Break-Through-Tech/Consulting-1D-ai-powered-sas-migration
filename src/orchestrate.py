"""Build and validate the Program 1 orchestration plan."""

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MANIFEST = (
    ROOT / "manifests" / "program1.json"
)

DEFAULT_GROUP_CONFIG = (
    ROOT / "config" / "measure_groups.json"
)

ORCHESTRATION_FIELDS = {
    "kind",
    "translate",
    "depends_on",
}

ALLOWED_KINDS = {
    "context",
    "macro",
    "group",
}


class OrchestrationConfigError(ValueError):
    """Raised when Program 1 orchestration metadata is invalid."""


def _load_json(path):
    """Load JSON from disk."""
    path = Path(path)

    with path.open(encoding="utf-8") as file:
        return json.load(file)


def load_manifest(manifest_path=DEFAULT_MANIFEST):
    """Load the Program 1 step manifest."""
    steps = _load_json(manifest_path)

    if not isinstance(steps, list):
        raise OrchestrationConfigError(
            "Program 1 manifest must contain a list of steps."
        )

    return steps


def load_measure_groups(config_path=DEFAULT_GROUP_CONFIG):
    """Load Program 1 measure-group configuration."""
    groups = _load_json(config_path)

    if not isinstance(groups, dict):
        raise OrchestrationConfigError(
            "Measure-group configuration must be an object."
        )

    return groups


def _validate_step(step, seen_ids):
    """Validate one step's orchestration metadata."""
    if not isinstance(step, dict):
        raise OrchestrationConfigError(
            "Every Program 1 step must be an object."
        )

    step_id = step.get("id")

    if (
        not isinstance(step_id, str)
        or not step_id.strip()
    ):
        raise OrchestrationConfigError(
            "Every Program 1 step must have a non-empty string id."
        )

    if step_id in seen_ids:
        raise OrchestrationConfigError(
            f"Duplicate Program 1 step id: {step_id}"
        )

    missing = ORCHESTRATION_FIELDS - set(step)

    if missing:
        raise OrchestrationConfigError(
            f"{step_id}: missing orchestration fields "
            f"{sorted(missing)}"
        )

    kind = step["kind"]

    if kind not in ALLOWED_KINDS:
        raise OrchestrationConfigError(
            f"{step_id}: unsupported kind {kind!r}. "
            f"Expected one of {sorted(ALLOWED_KINDS)}."
        )

    translate = step["translate"]

    if type(translate) is not bool:
        raise OrchestrationConfigError(
            f"{step_id}: translate must be true or false."
        )

    dependencies = step["depends_on"]

    if not isinstance(dependencies, list):
        raise OrchestrationConfigError(
            f"{step_id}: depends_on must be a list."
        )

    if any(
        not isinstance(dependency, str)
        or not dependency.strip()
        for dependency in dependencies
    ):
        raise OrchestrationConfigError(
            f"{step_id}: every dependency must be "
            "a non-empty string."
        )

    if len(dependencies) != len(set(dependencies)):
        raise OrchestrationConfigError(
            f"{step_id}: depends_on contains "
            "duplicate dependencies."
        )

    if kind == "context" and translate:
        raise OrchestrationConfigError(
            f"{step_id}: context steps cannot be translated."
        )

    if (
        kind in {"macro", "group"}
        and not translate
    ):
        raise OrchestrationConfigError(
            f"{step_id}: {kind} steps must be translated."
        )

    unavailable = [
        dependency
        for dependency in dependencies
        if dependency not in seen_ids
    ]

    if unavailable:
        raise OrchestrationConfigError(
            f"{step_id}: dependencies must appear "
            f"earlier in the manifest: {unavailable}"
        )

    if (
        kind == "group"
        and dependencies != ["grp_score_macro"]
    ):
        raise OrchestrationConfigError(
            f"{step_id}: Program 1 group steps must depend "
            "on grp_score_macro."
        )

    if (
        step_id == "ctx_libs"
        and kind != "context"
    ):
        raise OrchestrationConfigError(
            "ctx_libs must be a context step."
        )

    if (
        step_id == "grp_score_macro"
        and kind != "macro"
    ):
        raise OrchestrationConfigError(
            "grp_score_macro must be a macro step."
        )

    return step_id


def resolve_group_config(step, groups):
    """
    Resolve a group step to its measure configuration.

    Program 1 manifest IDs and config keys use different casing, so
    resolution uses their shared output-table name.
    """
    if step.get("kind") != "group":
        raise OrchestrationConfigError(
            f"{step.get('id', '<unknown>')}: "
            "only group steps have measure-group configuration."
        )

    outputs = step.get("outputs")

    if (
        not isinstance(outputs, list)
        or len(outputs) != 1
        or not isinstance(outputs[0], str)
        or not outputs[0]
    ):
        raise OrchestrationConfigError(
            f"{step.get('id', '<unknown>')}: "
            "group step must define exactly one output table."
        )

    output_table = outputs[0]

    matches = [
        (key, config)
        for key, config in groups.items()
        if isinstance(config, dict)
        and config.get("output_table") == output_table
    ]

    if len(matches) != 1:
        raise OrchestrationConfigError(
            f"{step['id']}: expected exactly one "
            f"measure-group config for {output_table}, "
            f"found {len(matches)}."
        )

    config_key, config = matches[0]

    measures = config.get("measures")

    if not isinstance(measures, list):
        raise OrchestrationConfigError(
            f"{config_key}: measures must be a list."
        )

    return config_key, config


def build_program1_plan(
    manifest_path=DEFAULT_MANIFEST,
    config_path=DEFAULT_GROUP_CONFIG,
):
    """
    Build Program 1's validated, ordered orchestration plan.

    This function intentionally enforces Program 1-specific rules,
    including its grp_score macro dependency and one-to-one mapping
    between group steps and measure-group configuration.
    """
    steps = load_manifest(manifest_path)
    groups = load_measure_groups(config_path)

    plan = []
    seen_ids = set()
    claimed_config_keys = []

    for step in steps:
        step_id = _validate_step(
            step,
            seen_ids,
        )

        planned_step = dict(step)

        planned_step["action"] = (
            "translate"
            if step["translate"]
            else "skip"
        )

        if step["kind"] == "group":
            config_key, config = resolve_group_config(
                step,
                groups,
            )

            claimed_config_keys.append(config_key)

            planned_step["group_config_key"] = config_key
            planned_step["group_config"] = dict(config)

        plan.append(planned_step)
        seen_ids.add(step_id)

    claim_counts = Counter(claimed_config_keys)

    duplicate_claims = sorted(
        key
        for key, count in claim_counts.items()
        if count > 1
    )

    if duplicate_claims:
        raise OrchestrationConfigError(
            "Multiple Program 1 steps claim the same "
            f"measure-group config: {duplicate_claims}"
        )

    unclaimed = sorted(
        set(groups) - set(claimed_config_keys)
    )

    if unclaimed:
        raise OrchestrationConfigError(
            "Program 1 measure-group config entries are "
            f"not claimed by any group step: {unclaimed}"
        )

    return plan


DEFAULT_SAS_OUTPUT_DIR = (
    ROOT / "data" / "Project_1" / "SAS Output CSV"
)


def run_program1(
    translate_step,
    execute_step,
    validate_output,
    manifest_path=DEFAULT_MANIFEST,
    config_path=DEFAULT_GROUP_CONFIG,
    sas_output_dir=DEFAULT_SAS_OUTPUT_DIR,
):
    """
    Run Program 1 through injected translation, execution, and validation steps.

    Component implementations are injected so this orchestration layer is not
    coupled to a specific translator or Python execution implementation.
    Retry and runtime exception handling are intentionally handled separately.
    """
    plan = build_program1_plan(
        manifest_path=manifest_path,
        config_path=config_path,
    )

    sas_output_dir = Path(sas_output_dir)

    step_results = []
    output_results = {}
    execution_outputs = {}

    for step in plan:
        if step["action"] == "skip":
            step_results.append(
                {
                    "id": step["id"],
                    "kind": step["kind"],
                    "status": "SKIPPED",
                }
            )
            continue

        generated_code = translate_step(step)

        dependency_outputs = {
            dependency: execution_outputs[dependency]
            for dependency in step["depends_on"]
        }

        execution_output = execute_step(
            step,
            generated_code,
            dependency_outputs,
        )

        execution_outputs[step["id"]] = execution_output

        step_result = {
            "id": step["id"],
            "kind": step["kind"],
            "status": "COMPLETE",
            "generated_code": generated_code,
        }

        if step["kind"] == "group":
            output_table = step["group_config"]["output_table"]

            sas_csv = (
                sas_output_dir
                / f"{output_table}.csv"
            )

            validation = validate_output(
                execution_output,
                sas_csv,
                "PROVIDER_ID",
            )

            if (
                not isinstance(validation, dict)
                or type(validation.get("passed")) is not bool
            ):
                raise ValueError(
                    "Validation result must contain a boolean "
                    "'passed' value."
                )

            passed = validation["passed"]

            status = (
                "PASS"
                if passed
                else "FAIL"
            )

            step_result["status"] = status
            step_result["output_table"] = output_table
            step_result["validation"] = validation

            output_results[output_table] = {
                "step_id": step["id"],
                "status": status,
                "validation": validation,
            }

        step_results.append(step_result)

    passed = all(
        output["status"] == "PASS"
        for output in output_results.values()
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "steps": step_results,
        "outputs": output_results,
    }
