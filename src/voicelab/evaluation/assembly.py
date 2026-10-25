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

from voicelab.contracts import (Attempt, Epoch, EvidenceRecord, Hash, Identifier, Model, RunManifest,
                               ScheduledTurn, TerminalOutcome, TurnResult, VoiceEvent)
from voicelab.reporting import write_report
from voicelab.reporting.diagnostics import calibration_report

from .artifacts import ArtifactReader, canonical_bytes, digest
from .gates import ReleaseVerifier, validate_gates
from .planning import validate_trial_plan
from .driver import convert_driver_events


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
    confirmation_artifact: str | None = None
    response_id: Identifier | None = None
    retry_count: int = Field(default=0, strict=True, ge=0)
    duplicate_count: int = Field(default=0, strict=True, ge=0)
    stages: list[dict[str, object]] = Field(default_factory=list)


class ConfirmationEvidence(Model):
    task_id: Identifier
    journal_hash: Hash
    trace_hash: Hash
    confirmation_turn_id: Identifier
    challenge_hash: Hash
    state_version: int = Field(strict=True, ge=0)
    response_epoch: Epoch
    slot_id: Literal['slot-a', 'slot-b']
    audio_artifact: str = Field(min_length=1, max_length=500)
    audio_hash: Hash


def _index(rows: list[dict], key: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        identity = row[key]
        if identity in result:
            raise ValueError("duplicate evidence identity: " + str(identity))
        result[identity] = row
    return result


def _trace_rows(raw: list[dict], manifest: dict, assignment: dict, correlations: list[dict], session_records: list[dict]) -> tuple[list[dict], list[str]]:
    output, issues, sequences, times = [], [], {}, {}
    mapping = {(r['observed_run_id'], r['observed_trial_id'], r['session_id'],
                r.get('observed_transport_turn_id') or r.get('observed_logical_turn_id')): r['logical_turn_id']
               for r in correlations}
    sessions = {(r['observed_run_id'], r['observed_trial_id'], r['session_id']) for r in correlations}
    sessions.update((r['observed_run_id'], r['observed_trial_id'], r['session_id']) for r in session_records)
    for index, row in enumerate(raw):
        try:
            event = VoiceEvent.model_validate(row).model_dump(mode="json")
        except ValueError:
            issues.append(f"trace record {index}: malformed canonical event")
            continue
        observed_session = (event['run_id'], event['trial_id'], event['session_id'])
        turn = mapping.get(observed_session + (event.get('transport_turn_id') or event.get('logical_turn_id'),))
        original_parent = mapping.get(observed_session + (event.get('logical_turn_id'),))
        if original_parent is not None:
            if turn is not None and turn != original_parent:
                issues.append('recovery transport mapping conflicts with original logical parent')
            turn = original_parent
        correlated = turn is not None
        if not correlated:
            turn = event['logical_turn_id']
        if event["run_id"] != manifest["run_id"] and observed_session not in sessions:
            raise ValueError("trace contains a different run")
        if turn is not None:
            if turn not in assignment:
                raise ValueError("trace turn outside scheduled denominator")
            if event['arm'] != assignment[turn]['arm'] or (not correlated and event['trial_id'] != assignment[turn]['trial_id']):
                raise ValueError("trace trial/arm differs from prespecified assignment")
            expected_boot = assignment[turn].get('observed_boot_id')
            if expected_boot is not None and event.get('boot_id') != expected_boot:
                issues.append('trace boot differs from observed ready mapping')
        # Internal join key only. Original canonical event IDs remain untouched.
        event['_scheduled_logical_turn_id'] = turn
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


LINEAGE_KEYS = ("trial_id", "owner_id", "session_id", "transport_turn_id", "connection_epoch", "response_epoch",
                "lineage_observation")


def _reservation_parents(raw: dict, parents: dict, correlations: list[dict], assignments: dict) -> list[str]:
    """Join exact reservation-time lineage to observed plan mappings; never by text, turn ID alone or totals."""
    unresolved = []
    for attempt in raw.get("attempts", []):
        identity = attempt["attempt_id"]
        if identity in parents:
            continue
        planned = attempt["logical_turn_id"] in assignments
        if attempt.get("lineage_observation") != "recorded_at_reservation":
            if not planned:
                unresolved.append(identity)
            continue
        # A sent transport ID alone cannot prove the server logical parent.
        matches = [row for row in correlations
                   if row.get("session_id") == attempt.get("session_id")
                   and row.get("observed_trial_id") == attempt.get("trial_id")
                   and row.get("observed_boot_id") == attempt.get("owner_id")
                   and all(type(attempt.get(key)) is int and row.get(key) == attempt[key]
                           for key in ('connection_epoch', 'response_epoch'))
                   and row.get("observed_transport_turn_id") == attempt.get("transport_turn_id")
                   and row.get("observed_logical_turn_id") == attempt["logical_turn_id"]]
        targets = {row["logical_turn_id"] for row in matches}
        related = [row for row in correlations
                   if row.get('session_id') == attempt.get('session_id')
                   and row.get('observed_transport_turn_id') == attempt.get('transport_turn_id')]
        if not targets and planned and not related:
            continue  # resolved by unchanged plan identity, not by a join
        if len(targets) != 1 or (planned and targets != {attempt["logical_turn_id"]}):
            unresolved.append(identity)
            continue
        match = matches[0]
        parents[identity] = {"attempt_id": identity, "logical_turn_id": next(iter(targets)),
                             "observed_logical_turn_id": attempt["logical_turn_id"],
                             "observed_run_id": match.get("observed_run_id"), "observed_trial_id": attempt["trial_id"],
                             "session_id": attempt["session_id"], "observed_boot_id": match.get("observed_boot_id"),
                             "observed_transport_turn_id": attempt["transport_turn_id"],
                             "connection_epoch": attempt["connection_epoch"],
                             "response_epoch": attempt["response_epoch"],
                             "source": "reservation_lineage"}
    return sorted(unresolved)


def _attempts(raw: dict, assignments: dict, correlations: list[dict],
              unresolved: list[str] | tuple = ()) -> tuple[list[dict], dict]:
    attempts, known, exposure, unknown = [], Decimal(0), Decimal(0), 0
    if "session_attempts" in raw or "attempt_lineage" in raw:
        raise ValueError("session_attempts is an exact-session projection, not the canonical whole-run ledger")
    source = raw.get("attempts", [])
    _index(source, "attempt_id")
    events = raw.get("events", [])
    by_correlation = _index(correlations, 'attempt_id')
    for original in source:
        row = dict(original)
        correlation = by_correlation.get(row['attempt_id'])
        if correlation:
            if row['logical_turn_id'] != correlation['observed_logical_turn_id']:
                raise ValueError('ledger source ID conflicts with observed driver correlation')
            if row.get('lineage_observation') == 'recorded_at_reservation' and any(
                    row.get(source) is None or row[source] != correlation.get(target)
                    for source, target in (
                        ('trial_id', 'observed_trial_id'), ('session_id', 'session_id'),
                        ('owner_id', 'observed_boot_id'), ('transport_turn_id', 'observed_transport_turn_id'),
                        ('connection_epoch', 'connection_epoch'), ('response_epoch', 'response_epoch'))):
                raise ValueError('ledger reservation lineage conflicts with observed driver correlation')
            row['logical_turn_id'] = correlation['logical_turn_id']
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
        # Reservation-time lineage is carried as recorded; missing context stays unknown.
        canonical.update({key: row[key] for key in LINEAGE_KEYS if row.get(key) is not None})
        checked = Attempt.model_validate(canonical).model_dump(mode="json")
        if correlation:
            checked["lineage_resolution"] = correlation.get("source", "driver_or_trace_correlation")
        elif checked["attempt_id"] in unresolved:
            checked["lineage_resolution"] = "unresolved_conflict"
        else:
            checked["lineage_resolution"] = "plan_identity"
        if not checked['basis'] or not checked['price_version']:
            raise ValueError('attempt price/bound provenance missing')
        if checked["state"] == "settled":
            if amount is None or not checked['usage_source']:
                raise ValueError("settled attempt lacks amount/authoritative usage source")
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
               "estimated_components": raw.get("estimated_components", []),
               "attempt_lineage": {
                   "recorded_at_reservation": sum(a["lineage_observation"] == "recorded_at_reservation" for a in attempts),
                   "unknown_attempt_ids": sorted(a["attempt_id"] for a in attempts if a["lineage_observation"] != "recorded_at_reservation"),
                   "scope": "canonical saved whole-run ledger rows; session_attempts projections are never summed",
                   "inferred_joins": False}}
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


def _trace_stages(children: list[dict]) -> list[dict]:
    spans = []
    for event in children:
        if event['kind'] != 'controller_end':
            continue
        explicit = (event.get('boot_id') is not None and event.get('span_start_ns') is not None
                    and event.get('span_end_ns') is not None and event.get('span_clock_domain') == 'server_monotonic_ns')
        spans.append({'name': 'controller', 'clock_domain': event.get('span_clock_domain', 'unknown'),
                      'clock_scope': str((event['session_id'], event.get('boot_id'), event['connection_epoch'])),
                      'start': event['span_start_ns'] if explicit else None,
                      'end': event['span_end_ns'] if explicit else None,
                      'observation': 'observed' if explicit else 'unknown',
                      'reason': None if explicit else 'missing explicit controller span; receipt stamps not substituted'})
    return spans


def _validate_journal(journal: dict, manifest: dict, correlations: list[dict]) -> None:
    if journal.get('run_id') == manifest['run_id']:
        return
    exports = journal.get('sessions', [journal] if 'session' in journal else [])
    allowed = {(r['observed_run_id'], r['observed_trial_id'], r['session_id']) for r in correlations}
    if not exports:
        raise ValueError('authority journal run/observed session binding missing')
    for export in exports:
        session = export['session']
        if (session['run_id'], session['trial_id'], session['session_id']) not in allowed:
            raise ValueError('authority journal outside observed driver session mapping')


def _oracle(row: dict | None, *, manifest: dict, assignment: dict, journal_hash: str,
            input_hash: str | None, output_hash: str | None, trace_hash: str,
            response_id: str | None, confirmation_hash: str | None, confirmation_audio_hash: str | None,
            verify_oracle: Callable[[dict], bool] | None) -> tuple[bool | None, bool | None, str | None, list[str]]:
    if row is None or verify_oracle is None or not verify_oracle(row):
        return None, None, None, ["independent adjudication not externally verified"]
    bindings = {"run_id": manifest["run_id"], "trial_id": assignment["trial_id"],
                "logical_turn_id": assignment["logical_turn_id"], "journal_hash": journal_hash,
                "input_audio_hash": input_hash, "rendered_audio_hash": output_hash, "trace_hash": trace_hash,
                'code_hash': manifest['code_hash'], 'build_hash': manifest['build_hash'],
                'fixture_manifest_hash': manifest['fixture_manifest_hash'], 'response_id': response_id,
                'confirmation_evidence_hash': confirmation_hash, 'confirmation_audio_hash': confirmation_audio_hash}
    if (any(row.get(key) != value for key, value in bindings.items()) or not input_hash or not output_hash
            or not response_id or not confirmation_hash or not confirmation_audio_hash):
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
    issues = [str(reason) for reason in row.get('unknown_reasons', [])]
    if task is None or spoken is None:
        issues.append('manual task/spoken interpretation remains unknown')
    return task, spoken, terminal, issues


def prepare_inputs(bundle: dict, artifact_root: str | Path, *, expected_freeze_hash: str | None = None,
                   verify_release: ReleaseVerifier | None = None, public_access=None) -> tuple:
    """Shared prospective-run and saved-evidence gates; no client/provider execution."""
    from .custody import PublicCustodyManifest
    from .exposure import PrivateExposureUse, PublicReadAccess
    public = PublicCustodyManifest.model_validate(bundle['public_manifest']).model_dump(mode='json')
    if bundle['artifacts']['manifest']['sha256'] != public['manifest_sha256']:
        raise ValueError('public scope does not pin actual run manifest')
    if isinstance(verify_release, PrivateExposureUse):
        reader = verify_release.reader(artifact_root, bundle['artifacts'])
    elif public['split'] == 'heldout':
        raise ValueError('private access requires actual redeemed in-memory exact use, not a callback/receipt flag')
    else:
        if not isinstance(public_access, PublicReadAccess):
            raise ValueError('actual external public-dev read/rights proof required; no CLI flag grants access')
        reader = public_access.reader(artifact_root, bundle['artifacts'])
    manifest = RunManifest.model_validate(reader.json("manifest", "manifest")).model_dump(mode="json")
    if any(manifest[key] != value for key, value in public.items() if key != 'manifest_sha256'):
        raise ValueError('public scope mismatches actual manifest; no plan/fixture access')
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
    freeze = bundle.get('public_freeze')
    release = bundle.get('public_release')
    gates = validate_gates(manifest, plan, fixture_manifest, freeze, reader,
                           expected_freeze_hash=expected_freeze_hash, release=release, verify_release=verify_release)
    return reader, manifest, plan, gates


def assemble_evaluation(bundle: dict, artifact_root: str | Path, *,
                        expected_freeze_hash: str | None = None,
                        verify_release: ReleaseVerifier | None = None,
                        verify_oracle: Callable[[dict], bool] | None = None, public_access=None) -> dict:
    """No I/O effects except bounded reads. Trust callbacks come from operator code, never artifacts."""
    reader, manifest, plan, gates = prepare_inputs(bundle, artifact_root,
        expected_freeze_hash=expected_freeze_hash, verify_release=verify_release, public_access=public_access)
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
    conversion = {'observations': [], 'correlations': [], 'attempt_correlations': [], 'task_admissions': [], 'issues': [],
                  'admission_evidence': [], 'auxiliary_sessions': [], 'wire_audio_rollups': [], 'attempt_lineage_unresolved': []}
    if 'driver' in bundle['artifacts']:
        conversion = convert_driver_events(reader.jsonl('driver', 'driver_observations'), plan,
                                           mode='live' if manifest['mode'] == 'live' else 'mock')
        observation_name = 'driver'
        observations = _index(conversion['observations'], 'logical_turn_id')
    else:
        observation_name = 'observations'
        observations = _index(reader.jsonl('observations', 'observations'), 'logical_turn_id')
    for turn, assignment in assignments.items():
        bindings = [row for row in conversion['correlations'] if row['logical_turn_id'] == turn]
        if not bindings:
            bindings = [row for row in conversion['task_admissions'] if row['task_id'] == assignment['task_id']]
        identities = {(row.get('observed_trial_id'), row.get('session_id'), row.get('observed_boot_id')) for row in bindings}
        if len(identities) == 1:
            observed_trial, observed_session, observed_boot = next(iter(identities))
            for field, value in (('observed_trial_id', observed_trial), ('observed_session_id', observed_session), ('observed_boot_id', observed_boot)):
                if assignment.get(field) is not None and assignment[field] != value:
                    raise ValueError('saved observed identity conflicts with hashed driver mapping')
                assignment[field] = value
            assignment['identity_evidence_reference'] = 'sha256:' + reader.verified[observation_name]
    raw_trace = reader.jsonl("trace", "trace")
    trace, integrity = _trace_rows(raw_trace, manifest, assignments, conversion['correlations'], conversion['task_admissions'])
    integrity += conversion['issues']
    # Exact trace attempt IDs can bind whole-run ledger rows. Never attribute an
    # entire run snapshot by the session that happened to capture it or sum totals.
    attempt_parents = {row['attempt_id']: row for row in conversion['attempt_correlations']}
    for event in trace:
        turn = event['_scheduled_logical_turn_id']
        if event.get('attempt_id') and turn is not None:
            parent = {'attempt_id': event['attempt_id'], 'logical_turn_id': turn,
                      'observed_logical_turn_id': event['logical_turn_id'],
                      'observed_run_id': event['run_id'], 'observed_trial_id': event['trial_id'],
                      'session_id': event['session_id'], 'observed_boot_id': event.get('boot_id'),
                      'observed_transport_turn_id': event.get('transport_turn_id'),
                      'connection_epoch': event['connection_epoch'], 'response_epoch': event['response_epoch']}
            old = attempt_parents.get(event['attempt_id'])
            if old and any(old.get(key) != parent[key] for key in ('logical_turn_id', 'observed_logical_turn_id', 'session_id')):
                raise ValueError('trace/driver attempt-parent lineage conflicts')
            if old and any(old.get(key) != parent[key] for key in parent
                           if key in old and old[key] is not None and parent[key] is not None):
                raise ValueError('trace/driver attempt-parent lineage conflicts')
            attempt_parents[event['attempt_id']] = {**(old or {}), **parent}
    raw_ledger = reader.json("ledger", "ledger")
    lineage_unresolved = _reservation_parents(raw_ledger, attempt_parents, conversion['correlations'], assignments)
    conversion['attempt_correlations'] = [attempt_parents[key] for key in sorted(attempt_parents)]
    # An attempt with no plan identity and no exact join cannot be attributed. The whole
    # run is refused rather than guessed or silently dropped from all-attempt totals.
    unattributable = [identity for identity in lineage_unresolved
                      if identity not in attempt_parents and next(row for row in raw_ledger.get('attempts', [])
                          if row['attempt_id'] == identity)['logical_turn_id'] not in assignments]
    if unattributable:
        raise ValueError('ledger attempt lineage unattributable; run refused, not guessed: ' + ', '.join(unattributable))
    attempts, ledger = _attempts(raw_ledger, assignments, conversion['attempt_correlations'], lineage_unresolved)
    adjudications = _index(reader.jsonl("adjudication", "adjudication"), "logical_turn_id") if "adjudication" in bundle["artifacts"] else {}
    # Journal is a hashed authority export, not a provider-reported task outcome.
    journal = reader.json("journal", "journal")
    _validate_journal(journal, manifest, conversion['correlations'] + conversion['task_admissions'])
    if set(observations) - set(assignments) or set(adjudications) - set(assignments):
        raise ValueError("observation/adjudication outside frozen schedule")
    rows, evidence, admission_issues, transport_attempts, review_bindings = [], [], [], {}, []
    for turn_id in plan["scheduled_turns"]:
        assignment = assignments[turn_id]
        supplied = observations.get(turn_id)
        observation = TurnObservation.model_validate(supplied) if supplied else None
        if observation and (observation.run_id, observation.trial_id) != (manifest["run_id"], assignment["trial_id"]):
            raise ValueError("observation run/trial differs from scheduled assignment")
        children = [event for event in trace if event['_scheduled_logical_turn_id'] == turn_id]
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
        trace_terminals = [event for event in children if event['kind'] == 'turn_ended']
        final_trace_outcome = trace_terminals[-1].get('terminal_outcome') if trace_terminals else None
        transport_attempts[turn_id] = [{'observed_transport_turn_id': event['transport_turn_id'],
            'observed_logical_turn_id': event['logical_turn_id'], 'session_id': event['session_id'],
            'boot_id': event.get('boot_id'), 'connection_epoch': event['connection_epoch'],
            'response_epoch': event['response_epoch'], 'kind': event['kind'],
            'timestamp': event['timestamp'], 'clock_domain': event['clock_domain'],
            'terminal_outcome': event.get('terminal_outcome')} for event in children
            if event['kind'] in {'turn_started', 'turn_ended'}]
        if observation and observation.outcome and final_trace_outcome and observation.outcome != final_trace_outcome:
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
                "sha256:" + reader.verified[observation_name] if observation and observation.admission != "unknown" else
                assignment["admission_evidence_reference"])
        reasons = [] if observation else ["missing driver terminal/source record"]
        reasons += local_integrity
        input_hash = _audio(reader, observation.input_audio_artifact if observation else None, reasons, "input")
        output_hash = _audio(reader, observation.rendered_audio_artifact if observation else None, reasons, "rendered")
        response_id = next((event['response_id'] for event in reversed(children)
                            if event.get('response_id') and event['kind'] in {'audio_sent', 'rendered_ack'}), None)
        if response_id is None and observation:
            response_id = observation.response_id
        confirmation_hash, confirmation_audio_hash, confirmation_audio_name = None, None, None
        if observation and observation.confirmation_artifact:
            try:
                proof = ConfirmationEvidence.model_validate(reader.json(observation.confirmation_artifact,
                    'confirmation_evidence')).model_dump(mode='json')
                if (proof.get('task_id') != assignment['task_id'] or proof.get('journal_hash') != reader.verified['journal']
                        or proof.get('trace_hash') != reader.verified['trace']
                        or not proof.get('confirmation_turn_id') or not proof.get('challenge_hash')
                        or type(proof.get('state_version')) is not int or proof['state_version'] < 0
                        or type(proof.get('response_epoch')) is not int or proof['response_epoch'] < 0
                        or proof.get('slot_id') not in {'slot-a', 'slot-b'}):
                    raise ValueError('confirmation evidence scope incomplete')
                confirmation_audio_name = proof['audio_artifact']
                confirmation_audio_hash = reader.audio(confirmation_audio_name)
                if confirmation_audio_hash != proof.get('audio_hash'):
                    raise ValueError('confirmation audio hash mismatch')
                confirmation_hash = reader.verified[observation.confirmation_artifact]
            except (OSError, KeyError, ValueError):
                confirmation_hash, confirmation_audio_hash, confirmation_audio_name = None, None, None
                reasons.append('genuine confirmation evidence missing/unbound; human review required')
        bound = {'run_id': manifest['run_id'], 'task_id': assignment['task_id'], 'trial_id': assignment['trial_id'],
                 'logical_turn_id': turn_id, 'response_id': response_id,
                 'code_hash': manifest['code_hash'], 'build_hash': manifest['build_hash'],
                 'fixture_manifest_hash': manifest['fixture_manifest_hash'], 'trial_plan_hash': manifest['trial_plan_hash'],
                 'input_audio_hash': input_hash, 'rendered_audio_hash': output_hash,
                 'journal_hash': reader.verified['journal'], 'trace_hash': reader.verified['trace'],
                 'ledger_hash': reader.verified['ledger'], 'confirmation_evidence_hash': confirmation_hash,
                 'confirmation_audio_hash': confirmation_audio_hash,
                 'observed_trial_id': assignment.get('observed_trial_id'),
                 'observed_session_id': assignment.get('observed_session_id'),
                 'observed_boot_id': assignment.get('observed_boot_id')}
        review_gaps = list(local_integrity)
        # Only live K is the licensed fixed control; mock K keeps its synthetic mock semantics.
        control = assignment['arm'] == 'K' and manifest['mode'] == 'live'
        for field in ('response_id', 'input_audio_hash', 'rendered_audio_hash') + (
                () if control else ('confirmation_evidence_hash', 'confirmation_audio_hash')):
            if not bound[field]:
                review_gaps.append('missing ' + field)
        names = {'input_audio': observation.input_audio_artifact if observation and input_hash else None,
                 'rendered_audio': observation.rendered_audio_artifact if observation and output_hash else None,
                 'confirmation_audio': confirmation_audio_name,
                 'confirmation': observation.confirmation_artifact if observation and confirmation_hash else None,
                 'journal': 'journal', 'trace': 'trace'}
        review_bindings.append({'item_id': turn_id, 'bindings': bound,
            'artifacts': {key: dict(reader.references[name]) if name else None for key, name in names.items()},
            'evidence_gaps': sorted(set(review_gaps))})
        if control:
            # Licensed fixed PCM control: no task applicability, spoken correctness unknown,
            # and control_complete is a receipt/render control, never task or booking success.
            task_ok, spoken_ok, task_terminal = None, None, None
            reasons.append('licensed fixed control: task not applicable; spoken correctness unknown')
            if turn_id in adjudications:
                reasons.append('adjudication supplied for K control ignored; no task/spoken verdict applies')
        else:
            task_ok, spoken_ok, task_terminal, oracle_issues = _oracle(adjudications.get(turn_id),
                manifest=manifest, assignment=assignment, journal_hash=reader.verified["journal"],
                input_hash=input_hash, output_hash=output_hash, trace_hash=reader.verified["trace"],
                response_id=response_id, confirmation_hash=confirmation_hash, confirmation_audio_hash=confirmation_audio_hash,
                verify_oracle=verify_oracle)
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
        outcome = (final_trace_outcome or (observation.outcome if observation and observation.outcome else "missing_terminal"))
        if local_integrity:
            outcome = "missing_terminal"
        if outcome == "success" and (task_ok is False or spoken_ok is False):
            outcome = "task_failure"
        if control and outcome == "success":
            outcome = "missing_terminal"
            reasons.append('K control reported task success; only control_complete is a valid K completion')
        if not control and outcome == "control_complete":
            outcome = "missing_terminal"
            reasons.append('control_complete reported outside licensed K control arm')
        trace_stages = _trace_stages(children)
        supplied_stages = observation.stages if observation else []
        if trace_stages:
            supplied_stages = [stage for stage in supplied_stages if stage.get('name') != 'controller']
        row = TurnResult(run_id=manifest["run_id"], trial_id=assignment["trial_id"], logical_turn_id=turn_id,
            arm=assignment["arm"], outcome=outcome, task_correct=task_ok, spoken_correct=spoken_ok,
            receipt_latency_ms=receipt, render_latency_ms=render,
            timing_boundary="browser_output_timestamp_estimate" if render is not None else "receipt_only" if receipt is not None else "unknown",
            calibration_valid=calibration, unknown_reason="; ".join(sorted(set(reasons))) or None,
            retry_count=max(observation.retry_count if observation else 0,
                            max(0, len({event['transport_turn_id'] for event in children if event['kind'] == 'turn_started'}) - 1)),
            duplicate_count=observation.duplicate_count if observation else 0,
            cost_complete=bool(attempt_children) and all(attempt["state"] == "settled" for attempt in attempt_children),
            stages=supplied_stages + trace_stages,
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
            '_access_reader': reader,
            "evidence": evidence, "gates": gates, "trace_integrity_issues": sorted(set(integrity + admission_issues)),
            'review_bindings': review_bindings,
            "driver_correlations": {**{key: conversion[key] for key in ('correlations', 'attempt_correlations', 'task_admissions', 'issues',
                                                                       'admission_evidence', 'auxiliary_sessions', 'wire_audio_rollups')},
                                    'attempt_lineage_unresolved': sorted(set(conversion['attempt_lineage_unresolved'] + lineage_unresolved)),
                                    'transport_attempts': transport_attempts},
            "source_hashes": dict(sorted(reader.verified.items())), "product_acceptance": False}


def write_evaluation(assembled: dict, out: str | Path) -> list[Path]:
    """Later activation only: deterministic assembly artifacts, then the existing reporter."""
    destination = Path(out)
    from .exposure import require_output_root
    require_output_root(assembled.get('_access_reader'), destination.parent)
    destination.mkdir(parents=False, exist_ok=False)
    names = {"manifest": "assembled-manifest.json", "ledger": "assembled-ledger.json",
             "gates": "gate-status.json", "source_hashes": "source-hashes.json", 'driver_correlations': 'driver-correlations.json'}
    written = []
    for key, name in names.items():
        require_output_root(assembled['_access_reader'], destination.parent)
        path = destination / name
        path.write_bytes(canonical_bytes(assembled[key]))
        written.append(path)
    for key, name in (("rows", "assembled-turns.jsonl"), ("attempts", "assembled-attempts.jsonl"),
                      ("evidence", "assembled-evidence.jsonl")):
        require_output_root(assembled['_access_reader'], destination.parent)
        path = destination / name
        path.write_bytes(b"".join(canonical_bytes(row) for row in assembled[key]))
        written.append(path)
    require_output_root(assembled['_access_reader'], destination.parent)
    written += write_report(destination / names["manifest"], destination / "assembled-turns.jsonl",
                            destination / names["ledger"], destination / "report",
                            evidence_path=destination / 'assembled-evidence.jsonl',
                            source_hashes_path=destination / 'source-hashes.json',
                            driver_correlations_path=destination / 'driver-correlations.json')
    return written
