"""Closed metadata projection at the producer boundary; no content/credential fields."""
from __future__ import annotations

import json
import math
import re


# Add a field here deliberately when a producer contract changes. Unknown fields
# are not copied simply because they lack a suspicious name. Lists retain all rows.
FIELDS = frozenset('''
schema_version kind run_id sequence timestamp clock_domain observation mode split
task_id planned_task_id trial_id planned_trial_id pair_id arm cohort family speaker
repeat control network_profile model_version fixture_id logical_turn_id logical_turn_ids
transport_turn_id planned_turn_position session_id server_trial_namespace
server_run_id observed_boot_id boot_id trial_namespace response_id operation_id
logical_operation_id attempt_id connection_epoch response_epoch protocol_version
type payload state outcome terminal terminal_outcome terminal_reason terminal_source
outcome_source code reason source status observed expected action_id fault
fault_schedule turn_position action action_attempted expected_outcome incomplete
admission admitted_observed browser_state browser_clock_observed byte_length direction
message_metadata response metadata snapshot baseline driver app events detail at seq
cursor latest_seq oldest_seq dropped_before_cursor dropped_total trace_cursor trace_dropped
event_count terminal_present trace_complete trace_hash trace_reference trace evidence_reference
references path sha256 manifest_hash audio_sha256 artifact_hash content_hash
artifact_persisted approved_synthetic input_audio_artifact rendered_audio_artifact
sample_rate sample_offset sample_index frame start_frame end_frame onset_frame
detected_frame detection_frame last_voiced_frame decision_frame context_time contextTime
performance_ms performance_time context_seconds output_latency_seconds output_timestamp
contextTime performanceTime outputLatency boundary acoustic_uncertainty physical_output
sample_rate_hz quantum quantum_samples quantum_frames scheduled_gap_ms observed_gap_ms
observed_quantum_ms cutoff_error_samples mapping_monotonic raw_events raw_anchors anchors
phase repeat_index required_repeats rows controls control_rejections injected_offset_ms
scheduled_frame requested_frame observed_frame cutoff_frame fence_frame last_old_rendered_frame
lastOldRenderedFrame cutoff_error_frames gap_frames gap_ms observed_gap_frames offset_ms
probe_id probe_kind probe_result actual_frame expected_frame render_frame output_frame
past_scheduling past_schedule_requested past_schedule_clamped offset_rejected
fence_ack_observed app_fence_cutoff_within_quantum stale_epoch_frame_refused
app_worklet_anchor_valid injected_offset_rejected past_scheduling_anchor_valid
internal_gate_pass full_gate_pass full_gate_complete complete repeat_count failures
simulation effect control_id seed receipt receipt_is_not_effect_proof producer_proof
evidence session task_journal task booking slot_id state_version effect_committed
duplicate ok original_operations attempts attempt_count truncated limit command arguments
selection_turn confirmation_turn epoch version id slots hours
ledger accounting budget known_cost unknown_count unknown_exposure total_exposure
currency component reservation settled price_version usage provider_requests amount
admission_halted basis total_cost input_units output_units terminal_observation
resume_required audio_replayed fence_epoch rendered_through_sample rendered_ack
responseDone cancelTriggered fenceCounter lastFence connection injected
liveTracks openSockets live_tracks open_sockets open_files owned_handles media_tracks
audio_context worklet source_nodes socket timer_references timers pending_closures
pending_permission acquisitions cleanup_pending contexts_closed cleanup_complete
state_observed cleanup_observed observation_statuses owned_handles_status
queue_seconds playback upload queued_frames queuedFrames consumedFrames
monotonic_seconds timestamp_ns rss_bytes files sockets async_tasks asyncio_tasks
event_loop_lag_ms browser_heap_bytes browser_heaps_by_context js_heap_bytes
server_observations metrics value unit scope handles after_warmup sample_index
resources process_scope sample_interval_seconds collection_duration_seconds
aggregate_turns attempted_turns observed_turns interruptions reconnects
interruption_attempts interruption_effects reconnect_attempts reconnect_effects
sessions_started sessions_ended contexts sessions_required sessions_observed
fourth_session_attempted fourth_session_refused refusal_observation
coverage_actions_are_attempts coverage_complete resource_gate_pass passed summary
profile max_seconds max_turns concurrency required_interruptions required_reconnects
elapsed_seconds stop_reason started_actions terminal_actions product_outcome
driver_completed completed counts driver_ended evidence_label product_acceptance
heldout_release independent_release_verified missing_evidence_is_failure_or_unknown
spoken_correctness acoustic_latency trace_may_be_truncated limitation limitations
error_type fault_control_or_observation_unavailable dimensions_complete
dimension_collisions complete_by_dimension disjoint custody_independence
rendered_source_sample frame_delta valid sample_anchor_valid gap_error_ms
render_graph_only output_timestamp_estimate raw_audio transcripts
request result before after options row cancel_control last_probe clock_alignment
pulse_frames pulseFrames cancel_frame cancelFrame firstFrame lastFrame requestedFrame
sampleRate nonSilentFrames pulse gapFrames first_frame last_frame count
sample_anchor_clock_domain cutoff_kind sample_anchor_valid sample_sd_ms errors_ms
cleanup_errors pending_context_close pending_permission raf listeners acquisitions
snapshots unknown queue_observation lastClose cancelAcks resumeCount staleDiscards
admitted_sessions fault_kind fault_status affected_boundary injected_count
provider_tool_id provider_transport_id settled_units reservation_basis pricing_hash
sample_interval_observed max_sample_interval_seconds cadence_complete
resources snapshots server_observations worklet_queue_seconds turn_spacing_seconds
fault_kind sample_rates terminal_turns sample_sd_ms errors_ms queue_observation
graph_id sample_id gap_frames intended_cutoff_frame cancel_after_frames
last_old_frame non_silent_frames cancel_origin observation_statuses mapping_observation
output_timestamp_context_seconds output_timestamp_performance_ms output_estimate_ms
event_queue_maxima queue_observation_counts next active_probe
ledger_artifact artifact_reference ledger_reference snapshot_hash run_snapshot_hash
run_snapshot_id snapshot_id snapshot_scope
bytes encoding json_ensure_ascii clock_scope artifacts turns started_ns started_utc
spoken_task_acceptance active_fault_controls adapter_counters faults fault_artifact
faults_artifact
wording_family physical_audio calibration cleanup playback_queue upload_queue
context_sample_rate calibrations namespace source_start inputStart outputStart
span_clock_domain span_start_ns span_end_ns
inputPerOutput inputLimit delaySeconds delayInputSamples input_rate output_rate
device_frame_base droppedFrames window_ms sustain_ms threshold_dbfs minimum_hot_samples
last_voiced_context_frame device_sample_rate active_tracks browser_send_ms browser_receive_ms
server_receive_ns server_send_ns server_received_ns server_sent_ns rtt_ms uncertainty_ms
deadline_ms original_boundary muted receipt mapping_observation
role utterance_id text_sha256 pcm_sha256 frame_count duration_seconds
fixture_source closed playing complete capture_return_state fence_scope
cohort_hash catalog_hash fixture_manifest_hash trial_plan_hash code_hash build_hash
config_hash schema_hash pricing_manifest_hash authorization_reference_hash
models requested_models returned_models returned_identity_status manifest_hashes
network window starts_at expires_at upload_delay_ms download_delay_ms audio_drop_fraction fault_seed
commit_policy enabled_arms budget_owner_reference_hash budget_owner_scope
achieved_counts obligations all_planned_tasks_retained full_cohort_complete
planned_tasks planned_turns admitted_tasks refused_tasks started_turns terminal_tasks
unique_attempt_count attributed_attempt_count unresolved_attempt_count attempt_count_scope
source_hashes fixture_count asset_count fixture_manifest_hash
asset_references catalog_id trial_plan_hash task_template_id scope_hash
observed_run_id observed_trial_id observed_logical_turn_id observed_transport_turn_id
audio_frame_count observed_message_count metadata_stream_sha256 audio_observation_stride
coalesced
live_config_hash run_manifest_hash requested_voice expected_returned_models
live_owner_root_hash
k_control_config_hash licensed_control approval_observation asset_observation asset_hashes
provider_generation_cost_usd provider_cost_scope other_costs task_applicability
control_only asset_id model_stages translation capability_reference
runtime_verified independent_verification fingerprint_observation actually_returned_voice lease_verified
reservation_terms dispatch_authorized dispatched started provider_request_id transport_source
snapshot_owner_id bound_violation_count bound_integrity bound_violation_attempt_count
dispatch_halt_active dispatch_allowed price_policy_fingerprints total_exposure_definition
model pricing_reference enforcement_reference bound_usd rate_unit rates usage_bounds usage_units
server_ns usage_source sent_source dimensions
prior_owner_id prior_state remote_charge terminal_row_preserved transport_send_returned usage_observation
session_attempts attempt_lineage lineage_observation unresolved_attempt_ids
other_session_attempts_excluded inferred_joins unassigned_run_attempt_count
artifact_scope_hash owned_root_binding path_hash device inode access_classification
phase_id phase_action phase_deadline_ns deadline_ns ended_ns session_deadline_ns
absolute_cap_ms phase_limit task_deadlines_retained captured_turn_created probe_success
calibration_phase_receipt calibration_phase_end calibration_phases calibration_artifact phases
actually_returned_models budget_usd budget_owner owner_id lease_observation
commitCount lastCommit lastCompletion
wire_validation registry validation metadata_sha256
'''.split())

JSON_CONTAINERS = frozenset({'payload', 'task', 'cost', 'arguments'})
DYNAMIC_CONTAINERS = frozenset({'references', 'counts', 'browser_heaps_by_context',
                                'complete_by_dimension', 'dimension_collisions'})
CONTROL_VALUES = {
    'status': frozenset('armed applied refused unresolved observed estimated unknown observed_api_state observed_empty observed_released pending active ended settled reserved clarification completed cancelled incomplete not_started'.split()),
    'source': frozenset('relay client_report restart_reconciliation mock_fault_simulation keyless_mock mock_debug_text provider_transcript_unverified'.split()),
    'reason': frozenset('ended_by_user silence_timeout permission_denied device_lost device_muted unsupported_audio abandoned service_restart transport_loss recovery_unknown protocol_error provider_error provider_timeout queue_overrun budget_halt success expected_interruption task_failure calibration_invalid driver_schedule_completed incomplete_observation_coverage driver_abandoned no_observed_admission task_stopped_before_turn earlier_task_termination bounded_driver_step_failed missing_terminal_hash_or_owned_trace explicit_owned_relay_artifact_root_absent previous_process_ended_without_terminal admission refused'.split()) | {'admission refused'},
}
CONTROL_VALUES['reason'] |= {
    'own_process_metrics_disabled',
    'keyless mock — diagnostic synthetic tone; no spoken-task acceptance',
    'adapter epoch fence', 'adapter sequence gap; skipped/cancelled events possible',
    'inactive transport turn', 'inactive response',
    'simulated transport drop at before_commit', 'simulated transport drop at after_commit',
    'mock proposal parser and authority attempt',
    'adapter-observed controller span; translation identity noop',
    'relay epoch advanced before provider cancellation',
    'no audio replay; task journal preserved', 'partial/lost rendered ACK',
    'echo only; browser and server clock domains remain separate',
    'durable effect fence unavailable during terminal cleanup',
    'adapter_close_unknown; inspect retained liability',
    'controller_span', 'response_done', 'cancel.ack', 'resume.ack', 'unknown',
    'mock_debug_text', 'provider_transcript_unverified', 'mock_tool_attempt',
    'mock_transport_drop', 'mock_provider_error', 'generation_complete_and_render_ack',
    'explicit_interrupt', 'reconnect_no_audio_replay', 'simulated_service_restart',
    'mock attempt aborted before terminal settlement',
}
CONTROL_VALUES['source'] |= {'driver_admission', 'provider_session_updated', 'provider_terminal_usage',
                             'local_no_provider_component'}
CONTROL_VALUES['reason'] |= {
    'authorized live source — runtime unverified; independent acceptance pending',
    'licensed fixed PCM control; descriptive receipt/render only; no speech understanding',
    'bounded local diagnostics phase; no capture or probe success asserted',
    'canceled', 'dispatch_outcome_unknown', 'injected_transport_usage_unverified',
    'local boundary refused', 'missing_authoritative_usage', 'missing_price_terms',
    'mock fault transport drop', 'usage_component_mismatch', 'invalid_usage_dimension',
    'missing_usage_dimension', 'unsupported_usage_amount', 'usage_unresolved',
    'observed_bound_breach_with_incomplete_usage',
}


def decoded_metadata(value: object) -> object:
    """Decode known metadata JSON cells for validation without changing saved bytes."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == 'payload' and isinstance(item, str):
                item = json.loads(item)
            result[key] = decoded_metadata(item)
        return result
    if isinstance(value, list):
        return [decoded_metadata(item) for item in value]
    return value


def observed_metadata(value: object, *, _container: str | None = None) -> object:
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            normalized = re.sub(r'[^a-z0-9]', '', str(key).casefold())
            if key == 'dispatch_authorized':
                if type(item) is bool:
                    output[key] = item
                continue
            if key == 'runtime_verified':
                if item is False:
                    output[key] = False
                continue
            if key in {'independent_verification', 'lease_verified'}:
                if item == 'unknown':
                    output[key] = item
                continue
            if _container in {'rates', 'usage_bounds', 'usage_units', 'price_policy_fingerprints'}:
                if not isinstance(key, str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*){0,7}', key) is None:
                    continue
                if (_container == 'usage_units' and isinstance(item, str) and item in {'tokens', 'seconds', 'requests', 'samples'}
                        or _container == 'price_policy_fingerprints' and isinstance(item, str) and re.fullmatch(r'[a-f0-9]{64}', item)
                        or _container in {'rates', 'usage_bounds'} and isinstance(item, str) and re.fullmatch(r'\d{1,18}(?:\.\d{1,18})?', item)):
                    output[key] = item
                continue
            if _container == 'usage':
                if not isinstance(key, str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,159}', key) is None:
                    continue
                if isinstance(item, dict):
                    output[key] = observed_metadata(item, _container='usage')
                elif type(item) in {str, int} and re.fullmatch(r'\d{1,18}(?:\.\d{1,18})?', str(item)):
                    output[key] = item
                continue
            if _container == 'manifest_hashes':
                if key in {'authorization', 'capabilities', 'pricing', 'rights', 'evaluation'} and (item is None or isinstance(item, str) and re.fullmatch(r'[a-f0-9]{64}', item)):
                    output[key] = item
                continue
            if key in {'authorization_reference_hash', 'budget_owner_reference_hash'}:
                if isinstance(item, str) and re.fullmatch(r'[a-f0-9]{64}', item):
                    output[key] = item
                continue
            if _container in {'models', 'requested_models', 'returned_models', 'expected_returned_models'}:
                if key in {'V', 'C', 'K', 'native', 'stt', 'tts'} and isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9_.:/@+-]{1,192}', item):
                    output[key] = item
                continue
            if _container == 'actually_returned_models':
                if key in {'native', 'stt', 'tts'} and isinstance(item, dict) and set(item) == {'value', 'observation', 'source'}:
                    model = item['value']
                    if (model is None or isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9_.:/@+-]{1,192}', model)) and item['observation'] in {'observed', 'unknown'} and item['source'] in {'provider_session_updated', 'unknown'}:
                        output[key] = dict(item)
                continue
            if _container == 'asset_hashes':
                if (isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,96}', key)
                        and isinstance(item, str) and re.fullmatch(r'[a-f0-9]{64}', item)):
                    output[key] = item
                continue
            if (normalized in {'text', 'pcm16', 'audio', 'apikey', 'cookie', 'cookies'}
                    or any(part in normalized for part in ('nonce', 'auth', 'header', 'transcript', 'token', 'credential', 'secret'))):
                if key == 'transcripts' and item is False:
                    output[key] = False
                continue
            if key == 'samples':
                # Resource rows are metadata; PCM/sample arrays are never accepted.
                if isinstance(item, list) and all(isinstance(row, dict) and 'monotonic_seconds' in row
                                                   and 'server_observations' in row for row in item):
                    output[key] = [observed_metadata(row) for row in item]
                continue
            dynamic = (_container in DYNAMIC_CONTAINERS and isinstance(key, str)
                       and re.fullmatch(r'[A-Za-z0-9_-]{1,96}', key) is not None)
            if _container in DYNAMIC_CONTAINERS:
                if not dynamic:
                    continue
                if _container == 'counts' and (type(item) is not int or item < 0):
                    continue
                if _container == 'browser_heaps_by_context' and not (item is None or
                        type(item) in {int, float} and math.isfinite(item) and item >= 0):
                    continue
                if _container == 'references' and not (isinstance(item, dict)
                        and set(item) == {'path', 'kind', 'sha256'}
                        and isinstance(item['path'], str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,180}', item['path'])
                        and isinstance(item['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', item['sha256'])
                        and isinstance(item['kind'], str) and re.fullmatch(r'[a-z_]{1,80}', item['kind'])):
                    continue
                if _container == 'complete_by_dimension' and type(item) is not bool:
                    continue
                if _container == 'dimension_collisions' and not (isinstance(item, list) and all(
                        isinstance(label, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', label) for label in item)):
                    continue
            if key not in FIELDS and not dynamic:
                continue
            # Flags about content are permitted only as false, never content containers.
            if key in {'raw_audio', 'transcripts'}:
                if item is False:
                    output[key] = False
                continue
            if key in JSON_CONTAINERS and isinstance(item, str):
                try:
                    item = json.loads(item)
                except (ValueError, TypeError):
                    continue
                if not isinstance(item, dict):
                    continue
            if key in JSON_CONTAINERS | {'message_metadata'} and not isinstance(item, dict):
                continue
            if key in {'events', 'attempts', 'rows', 'artifacts', 'turns'} and not (
                    isinstance(item, list) and all(isinstance(row, dict) for row in item)):
                continue
            if key == 'detail' and not isinstance(item, (dict, list)):
                continue
            if key in CONTROL_VALUES and isinstance(item, str) and item not in CONTROL_VALUES[key]:
                continue
            output[key] = observed_metadata(item, _container=key)
        return output
    if isinstance(value, (list, tuple)):
        return [observed_metadata(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None
