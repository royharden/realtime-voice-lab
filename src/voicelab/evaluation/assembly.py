"""Join saved source evidence once, then hand it to the canonical reporter.

No provider/client execution, scorer inference or self-issued independent verdict.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field

from voicelab.contracts import (Attempt, EvidenceRecord, Identifier, Model, RunManifest,
                               ScheduledTurn, TerminalOutcome, TurnResult, VoiceEvent)
from voicelab.reporting import write_report
from voicelab.reporting.diagnostics import calibration_report

from .artifacts import ArtifactReader, canonical_bytes, digest
from .gates import ReleaseVerifier, validate_gates
from .planning import validate_trial_plan


class TurnObservation(Model):
    run_id: Identifier
    trial_id: Identifier
    logical_turn_id: Identifier
    admission: Literal["started", "refused", "not_started", "unknown"] = "unknown"
    outcome: TerminalOutcome | None = None
    # Driver sidecar browser anchors: no cross-domain subtraction is allowed.
    timing_domain: Literal["browser_performance_ms", "unknown"] = "unknown"
    speech_end_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    first_received_pcm_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    first_nonsilent_render_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    rendered_audio_artifact: str | None = None
    input_audio_artifact: str | None = None
    calibration_artifact: str | None = None
    retry_count: int = Field(default=0, strict=True, ge=0)
    duplicate_count: int = Field(default=0, strict=True, ge=0)
    stages: list[dict[str, object]] = Field(default_factory=list)


def _index(rows: list[dict], key: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        identity = row[key]
        if identity in result:
            raise ValueError("duplicate evidence identity: " + str(identity))
        result[identity] = row
    return result


def _trace_rows(raw: list[dict], manifest: dict, assignment: dict) -> tuple[list[dict], list[str]]:
    output, issues, sequences, times = [], [], {}, {}
    for index, row in enumerate(raw):
        try:
            event = VoiceEvent.model_validate(row).model_dump(mode="json")
        except ValueError:
            issues.append(f"trace record {index}: malformed canonical event")
            continue
        if event["run_id"] != manifest["run_id"]:
            raise ValueError("trace contains a different run")
        turn = event["logical_turn_id"]
        if turn is not None:
            if turn not in assignment:
                raise ValueError("trace turn outside scheduled denominator")
            if (event["trial_id"], event["arm"]) != (assignment[turn]["trial_id"], assignment[turn]["arm"]):
                raise ValueError("trace trial/arm differs from prespecified assignment")
        key = (event["session_id"], event.get("boot_id"), event["connection_epoch"], event["clock_domain"])
        if event["sequence"] <= sequences.get(key, -1):
            issues.append(f"trace record {index}: duplicate/reordered sequence")
        sequences[key] = event["sequence"]
        if event["timestamp"] < times.get(key, 0):
            issues.append(f"trace record {index}: nonmonotonic clock")
        times[key] = event["timestamp"]
        if event["kind"] == "integrity_error":
            issues.append(f"trace record {index}: producer integrity error")
        output.append(event)
    return output, issues


def _attempts(raw: dict, assignments: dict) -> tuple[list[dict], dict]:
    attempts, known, exposure, unknown = [], Decimal(0), Decimal(0), 0
    source = raw.get("attempts", [])
    _index(source, "attempt_id")
    events = raw.get("events", [])
    for row in source:
        if row["logical_turn_id"] not in assignments or row["arm"] != assignments[row["logical_turn_id"]]["arm"]:
            raise ValueError("ledger attempt outside run/arm assignment")
        usage = [event for event in events if event.get("attempt_id") == row["attempt_id"] and event.get("kind") == "settled"]
        payload = usage[0].get("payload", {}) if len(usage) == 1 else {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        amount = row.get("settled")
        canonical = {key: row[key] for key in ("attempt_id", "logical_turn_id", "arm", "component", "reservation", "basis", "price_version", "state")}
        canonical.update(currency=row.get("currency", raw.get("currency", "USD")), settled=amount,
                         provider_request_id=row.get("provider_request_id"), units=row.get("units", {}),
                         usage_source=row.get("usage_source", payload.get("source")),
                         terminal_reason=row.get("terminal_reason", row.get("reason")))
        checked = Attempt.model_validate(canonical).model_dump(mode="json")
        if checked["state"] == "settled":
            if amount is None:
                raise ValueError("settled attempt lacks amount")
            known += Decimal(amount)
        else:
            exposure += Decimal(checked["reservation"])
            unknown += 1
        # Retain explicit start evidence alongside the canonical Attempt, never invent it.
        for key in ("started", "dispatched"):
            if row.get(key) is True:
                checked[key] = True
        attempts.append(checked)
    summary = {"known_cost": str(known), "unknown_count": unknown, "unknown_exposure": str(exposure),
               "complete": unknown == 0, "total_exposure": str(known + exposure), "currency": "USD",
               "attempts": sorted(attempts, key=lambda row: row["attempt_id"]), "events": events,
               "estimated_components": raw.get("estimated_components", [])}
    for key in ("known_cost", "unknown_exposure", "total_exposure"):
        if key in raw and Decimal(str(raw[key])) != Decimal(summary[key]):
            raise ValueError("ledger summary disagrees with immutable attempt rows: " + key)
    if "unknown_count" in raw and raw["unknown_count"] != unknown:
        raise ValueError("ledger unresolved count disagrees with attempts")
    return attempts, summary


def _audio(reader: ArtifactReader, name: str | None, reasons: list[str], label: str) -> str | None:
    if name is None:
        reasons.append(label + " audio unavailable")
        return None
    try:
        return reader.audio(name)
    except (OSError, KeyError, ValueError):
        reasons.append(label + " audio unavailable, unapproved or hash-invalid")
        return None


def _oracle(row: dict | None, *, manifest: dict, assignment: dict, journal_hash: str,
            input_hash: str | None, output_hash: str | None, trace_hash: str,
            verify_oracle: Callable[[dict], bool] | None) -> tuple[bool | None, bool | None, str | None, list[str]]:
    if row is None or verify_oracle is None or not verify_oracle(row):
        return None, None, None, ["independent adjudication not externally verified"]
    bindings = {"run_id": manifest["run_id"], "trial_id": assignment["trial_id"],
                "logical_turn_id": assignment["logical_turn_id"], "journal_hash": journal_hash,
                "input_audio_hash": input_hash, "rendered_audio_hash": output_hash, "trace_hash": trace_hash}
    if any(row.get(key) != value for key, value in bindings.items()) or not input_hash or not output_hash:
        return None, None, None, ["adjudication evidence binding incomplete/mismatched"]
    if row.get("method") != "independent_manual_listening_and_authority_journal":
        return None, None, None, ["provider transcript or nonindependent oracle is not acceptable"]
    if row.get("confirmation_evidence_reviewed") is not True:
        return None, None, None, ["genuine explicit confirmation evidence not independently checked"]
    task, spoken = row.get("task_correct"), row.get("spoken_correct")
    if task is not None and type(task) is not bool or spoken is not None and type(spoken) is not bool:
        raise ValueError("oracle correctness must be boolean or unknown")
    terminal = row.get("task_terminal")
    if terminal == "success" and (task is not True or spoken is not True):
        terminal = None
    return task, spoken, terminal, []


def assemble_evaluation(bundle: dict, artifact_root: str | Path, *,
                        expected_freeze_hash: str | None = None,
                        verify_release: ReleaseVerifier | None = None,
                        verify_oracle: Callable[[dict], bool] | None = None) -> dict:
    """No I/O effects except bounded reads. Trust callbacks come from operator code, never artifacts."""
    reader = ArtifactReader(artifact_root, bundle["artifacts"])
    manifest = RunManifest.model_validate(reader.json("manifest", "manifest")).model_dump(mode="json")
    plan = reader.json("plan", "plan")
    validate_trial_plan(plan)
    if manifest["split"] == "heldout" and verify_release is None:
        raise ValueError("heldout closed before fixture access: no independent release verifier")
    if (set(manifest["scheduled_turns"]) != set(plan["scheduled_turns"])
            or set(manifest["scheduled_tasks"]) != set(plan["scheduled_tasks"])):
        raise ValueError("manifest denominator differs from frozen trial plan")
    fixture_manifest = reader.json("fixtures", "fixtures")
    if reader.verified["fixtures"] != manifest["fixture_manifest_hash"]:
        raise ValueError("fixture manifest not pinned by run manifest")
    freeze = reader.json("freeze", "freeze") if "freeze" in bundle["artifacts"] else None
    release = reader.json("release", "release") if "release" in bundle["artifacts"] else None
    gates = validate_gates(manifest, plan, fixture_manifest, freeze, reader,
                           expected_freeze_hash=expected_freeze_hash, release=release, verify_release=verify_release)
    assignments = {row["logical_turn_id"]: ScheduledTurn.model_validate(row).model_dump(mode="json")
                   for row in plan["turn_assignments"]}
    manifest_assignments = _index(manifest["turn_assignments"], "logical_turn_id")
    if set(manifest_assignments) - set(assignments):
        raise ValueError("manifest assignment outside frozen plan")
    for turn, supplied_assignment in manifest_assignments.items():
        if any(supplied_assignment[key] != assignments[turn][key] for key in (
                "trial_id", "arm", "cohort", "network_profile", "model_version", "task_id", "family", "pair_id")):
            raise ValueError("manifest cohort assignment differs from frozen plan")
        if supplied_assignment["admission_evidence_reference"]:
            for key in ("admission", "admission_evidence_reference"):
                assignments[turn][key] = supplied_assignment[key]
    raw_trace = reader.jsonl("trace", "trace")
    trace, integrity = _trace_rows(raw_trace, manifest, assignments)
    raw_ledger = reader.json("ledger", "ledger")
    attempts, ledger = _attempts(raw_ledger, assignments)
    observations = _index(reader.jsonl("observations", "observations"), "logical_turn_id")
    adjudications = _index(reader.jsonl("adjudication", "adjudication"), "logical_turn_id") if "adjudication" in bundle["artifacts"] else {}
    # Journal is a hashed authority export, not a provider-reported task outcome.
    journal = reader.json("journal", "journal")
    if journal.get("run_id") != manifest["run_id"]:
        raise ValueError("authority journal belongs to a different run")
    if set(observations) - set(assignments) or set(adjudications) - set(assignments):
        raise ValueError("observation/adjudication outside frozen schedule")
    rows, evidence, admission_issues = [], [], []
    for turn_id in plan["scheduled_turns"]:
        assignment = assignments[turn_id]
        supplied = observations.get(turn_id)
        observation = TurnObservation.model_validate(supplied) if supplied else None
        if observation and (observation.run_id, observation.trial_id) != (manifest["run_id"], assignment["trial_id"]):
            raise ValueError("observation run/trial differs from scheduled assignment")
        children = [event for event in trace if event["logical_turn_id"] == turn_id]
        attempt_children = [attempt for attempt in attempts if attempt["logical_turn_id"] == turn_id]
        traced_start = any(event["kind"] in {"turn_started", "audio_received", "commit_received"} for event in children)
        baseline = assignment["admission"] if assignment["admission_evidence_reference"] else "unknown"
        admission = observation.admission if observation and observation.admission != "unknown" else baseline
        local_integrity = list(integrity)
        claims = {value for value in (baseline, admission) if value not in {"scheduled", "unknown"}}
        if traced_start:
            claims.add("started")
        if len(claims) > 1:
            issue = "conflicting manifest/trace/driver admission: " + turn_id
            local_integrity.append(issue)
            admission_issues.append(issue)
        if "started" in claims:
            admission = "started"
        trace_terminals = {event.get("terminal_outcome") for event in children
                           if event["kind"] == "turn_ended" and event.get("terminal_outcome") is not None}
        if len(trace_terminals) > 1 or (observation and observation.outcome and trace_terminals
                                      and observation.outcome not in trace_terminals):
            issue = "conflicting trace/driver terminal: " + turn_id
            local_integrity.append(issue)
            admission_issues.append(issue)
        if traced_start:
            if admission in {"refused", "not_started"}:
                issue = "trace start conflicts with driver admission: " + turn_id
                local_integrity.append(issue)
                admission_issues.append(issue)
            admission = "started"
        assignment["admission"] = admission
        if admission != "unknown":
            assignment["admission_evidence_reference"] = (
                "sha256:" + reader.verified["trace"] if traced_start else
                "sha256:" + reader.verified["observations"] if observation and observation.admission != "unknown" else
                assignment["admission_evidence_reference"])
        reasons = [] if observation else ["missing driver terminal/source record"]
        reasons += local_integrity
        input_hash = _audio(reader, observation.input_audio_artifact if observation else None, reasons, "input")
        output_hash = _audio(reader, observation.rendered_audio_artifact if observation else None, reasons, "rendered")
        task_ok, spoken_ok, task_terminal, oracle_issues = _oracle(adjudications.get(turn_id),
            manifest=manifest, assignment=assignment, journal_hash=reader.verified["journal"],
            input_hash=input_hash, output_hash=output_hash, trace_hash=reader.verified["trace"], verify_oracle=verify_oracle)
        reasons += oracle_issues
        if local_integrity:
            task_ok, spoken_ok, task_terminal = None, None, None
        calibration = None
        if observation and observation.calibration_artifact:
            calibration_raw = reader.json(observation.calibration_artifact, "calibration")
            if reader.verified[observation.calibration_artifact] == manifest["calibration_hash"]:
                calibration = calibration_report(calibration_raw)["internal_gate_pass"]
            else:
                reasons.append("calibration evidence does not match run manifest")
        receipt, render = None, None
        if observation and observation.timing_domain == "browser_performance_ms" and not local_integrity:
            for metric, anchor in (("receipt", observation.first_received_pcm_ms), ("render", observation.first_nonsilent_render_ms)):
                if observation.speech_end_ms is not None and anchor is not None:
                    delta = anchor - observation.speech_end_ms
                    if delta < 0:
                        reasons.append(metric + " anchor precedes speech end")
                    elif metric == "receipt":
                        receipt = delta
                    else:
                        render = delta
        outcome = (observation.outcome if observation and observation.outcome else
                   next(iter(trace_terminals)) if len(trace_terminals) == 1 else "missing_terminal")
        if local_integrity:
            outcome = "missing_terminal"
        if outcome == "success" and (task_ok is False or spoken_ok is False):
            outcome = "task_failure"
        row = TurnResult(run_id=manifest["run_id"], trial_id=assignment["trial_id"], logical_turn_id=turn_id,
            arm=assignment["arm"], outcome=outcome, task_correct=task_ok, spoken_correct=spoken_ok,
            receipt_latency_ms=receipt, render_latency_ms=render,
            timing_boundary="browser_output_timestamp_estimate" if render is not None else "receipt_only" if receipt is not None else "unknown",
            calibration_valid=calibration, unknown_reason="; ".join(sorted(set(reasons))) or None,
            retry_count=observation.retry_count if observation else 0,
            duplicate_count=observation.duplicate_count if observation else 0,
            cost_complete=bool(attempt_children) and all(attempt["state"] == "settled" for attempt in attempt_children),
            stages=observation.stages if observation else [],
            admission=admission if admission != "not_started" else "unknown",
            cohort=assignment["cohort"], task_id=assignment["task_id"], task_terminal=task_terminal,
            network_profile=assignment["network_profile"], model_version=assignment["model_version"],
            family=assignment["family"], pair_id=assignment["pair_id"],
            timing_domain=observation.timing_domain if observation else "unknown").model_dump(mode="json")
        rows.append(row)
        record = EvidenceRecord(run_id=manifest["run_id"], trial_id=assignment["trial_id"],
            trace_hash=reader.verified["trace"], audio_hash=output_hash, ledger_hash=reader.verified["ledger"],
            oracle_hash=reader.verified.get("adjudication"), result_hash=digest(row),
            custody_reference=gates["custody_reference"], observation="unknown" if reasons else "observed",
            unknown_reason=row["unknown_reason"]).model_dump(mode="json")
        evidence.append({"logical_turn_id": turn_id, "record": record})
    manifest["turn_assignments"] = [assignments[turn] for turn in plan["scheduled_turns"]]
    return {"manifest": manifest, "rows": rows, "attempts": attempts, "ledger": ledger,
            "evidence": evidence, "gates": gates, "trace_integrity_issues": sorted(set(integrity + admission_issues)),
            "source_hashes": dict(sorted(reader.verified.items())), "product_acceptance": False}


def write_evaluation(assembled: dict, out: str | Path) -> list[Path]:
    """Later activation only: deterministic assembly artifacts, then the existing reporter."""
    destination = Path(out)
    destination.mkdir(parents=True, exist_ok=True)
    names = {"manifest": "assembled-manifest.json", "ledger": "assembled-ledger.json",
             "gates": "gate-status.json", "source_hashes": "source-hashes.json"}
    written = []
    for key, name in names.items():
        path = destination / name
        path.write_bytes(canonical_bytes(assembled[key]))
        written.append(path)
    for key, name in (("rows", "assembled-turns.jsonl"), ("attempts", "assembled-attempts.jsonl"),
                      ("evidence", "assembled-evidence.jsonl")):
        path = destination / name
        path.write_bytes(b"".join(canonical_bytes(row) for row in assembled[key]))
        written.append(path)
    written += write_report(destination / names["manifest"], destination / "assembled-turns.jsonl",
                            destination / names["ledger"], destination / "report")
    return written
