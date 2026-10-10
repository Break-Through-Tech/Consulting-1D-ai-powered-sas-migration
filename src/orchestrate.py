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
