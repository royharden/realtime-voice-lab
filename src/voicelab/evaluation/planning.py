"""Balanced matched-task schedule. Creating a plan never runs an experiment."""
from __future__ import annotations

import random

from voicelab.contracts import Identifier, Model
from pydantic import Field

from .artifacts import digest


class PlanRequest(Model):
    run_id: Identifier
    seed: int = Field(strict=True, ge=0, le=2**32 - 1)
    families: list[str]
    speakers: list[str]
    network_profile: str = Field(min_length=1, max_length=500)
    models: dict[str, str]
    # Exactly one fixture for each selected family/speaker cell.
    fixture_cells: list[dict[str, str]]
    fault_schedules: dict[str, list[dict[str, object]]]
    turns_per_task: int = Field(default=8, strict=True, ge=1, le=8)


def create_trial_plan(request: dict) -> dict:
    spec = PlanRequest.model_validate(request)
    families, speakers = sorted(spec.families), sorted(spec.speakers)
    if len(families) != 8 or len(set(families)) != 8 or len(speakers) != 2 or len(set(speakers)) != 2:
        raise ValueError("paired comparison requires exactly eight families and two speakers")
    if set(spec.models) != {"V", "C", "K"} or not all(spec.models.values()):
        raise ValueError("all three pinned pipeline identities required")
    cells = {(row["family"], row["speaker"]): row["fixture_id"] for row in spec.fixture_cells}
    if len(cells) != len(spec.fixture_cells) or set(cells) != {(f, s) for f in families for s in speakers}:
        raise ValueError("exactly one prespecified fixture per selected cell required")
    if set(spec.fault_schedules) != set(families) or not all(spec.fault_schedules.values()):
        raise ValueError("one nonempty frozen fault schedule per family required")
    for schedule in spec.fault_schedules.values():
        for action in schedule:
            turn = action.get("turn_position")
            if type(turn) is not int or not 1 <= turn <= spec.turns_per_task or not action.get("kind"):
                raise ValueError("fault action requires bounded turn_position and named kind")
    rng = random.Random(spec.seed)
    # 32 base pairs plus one extra pair/family; extra speaker alternates 4/4.
    clean = [(f, s, repetition) for f in families for s in speakers for repetition in (0, 1)]
    clean += [(f, speakers[index % 2], 2) for index, f in enumerate(families)]
    fault = [(f, speakers[index % 2], 0) for index, f in enumerate(families)]
    pairs, tasks, assignments = [], [], []
    for cohort, selected in (("clean", clean), ("fault", fault)):
        rng.shuffle(selected)
        orders = ["AB", "BA"] * (len(selected) // 2)
        rng.shuffle(orders)
        for (family, speaker, repetition), order in zip(selected, orders):
            pair_id = f"pair-{len(pairs) + 1:03d}"
            fault_actions = spec.fault_schedules[family] if cohort == "fault" else []
            pairs.append({"pair_id": pair_id, "cohort": cohort, "family": family, "speaker": speaker,
                          "repeat": repetition, "order": order, "fixture_id": cells[family, speaker],
                          "fault_schedule": fault_actions})
            # K is a separate control task; it never enters V/C order or paired differences.
            for arm in (("V", "C") if order == "AB" else ("C", "V")) + ("K",):
                task_id = f"{pair_id}-{arm}"
                turns = []
                for position in range(1, spec.turns_per_task + 1):
                    turn_id = f"{task_id}-t{position:02d}"
                    turns.append(turn_id)
                    assignments.append({"logical_turn_id": turn_id, "trial_id": task_id, "task_id": task_id,
                        "arm": arm, "cohort": cohort, "family": family,
                        "pair_id": f"{pair_id}-t{position:02d}" if arm != "K" else None,
                        "network_profile": spec.network_profile, "model_version": spec.models[arm],
                        "admission": "unknown", "admission_evidence_reference": None})
                tasks.append({"task_id": task_id, "trial_id": task_id, "pair_id": pair_id,
                    "arm": arm, "cohort": cohort, "family": family, "speaker": speaker,
                    "repeat": repetition, "network_profile": spec.network_profile, "model_version": spec.models[arm],
                    "fixture_id": cells[family, speaker], "fault_schedule": fault_actions,
                    "logical_turn_ids": turns, "control": arm == "K"})
    body = {"schema_version": 1, "run_id": spec.run_id, "seed": spec.seed,
            "request": spec.model_dump(mode="json"),
            "design": "40 clean + 8 fault matched task pairs; 48 separate K controls",
            "turns_per_task": spec.turns_per_task, "pairs": pairs, "tasks": tasks,
            "turn_assignments": assignments, "scheduled_tasks": [task["task_id"] for task in tasks],
            "scheduled_turns": [row["logical_turn_id"] for row in assignments],
            "evidence_status": "prespecified schedule; no execution or acceptance"}
    return {**body, "plan_hash": digest(body)}


def validate_trial_plan(plan: dict) -> None:
    body = {key: value for key, value in plan.items() if key != "plan_hash"}
    if digest(body) != plan.get("plan_hash"):
        raise ValueError("trial plan hash mismatch")
    # Reconstruct the prescribed design, not just a caller's rehashed arbitrary list.
    if plan != create_trial_plan(plan["request"]):
        raise ValueError("trial plan differs from its prespecified balanced design")
    pairs, tasks = plan["pairs"], plan["tasks"]
    if len({pair["pair_id"] for pair in pairs}) != 48 or len(pairs) != 48 or len(tasks) != 144:
        raise ValueError("incomplete paired task/control schedule")
    for cohort, count in (("clean", 40), ("fault", 8)):
        selected = [pair for pair in pairs if pair["cohort"] == cohort]
        if len(selected) != count or sum(p["order"] == "AB" for p in selected) != count // 2:
            raise ValueError("cohort AB/BA balance changed")
    ids = [row["logical_turn_id"] for row in plan["turn_assignments"]]
    if ids != plan["scheduled_turns"] or len(ids) != len(set(ids)):
        raise ValueError("scheduled logical turn assignments changed")
    if [task["task_id"] for task in tasks] != plan["scheduled_tasks"]:
        raise ValueError("scheduled task assignments changed")
