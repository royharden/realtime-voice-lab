# Browser diagnostics API v1

Source implementation only. Runtime, device calibration, cleanup behavior and physical sound remain unverified. The global is `window.__voiceLabDiagnostics` (frozen, non-writable).

## Snapshot and trace

`snapshot()` returns schema_version 1, state, mode, arm, browser-performance timestamp/domain, owned_handles, queue_seconds, observation_statuses, worklet, active_probe, clock_alignment, last_probe, trace_cursor and trace_dropped.

Owned counts are browser API/tracker observations: media_tracks, audio_context, worklet, source, socket, timers, raf, object_urls, listeners, pending_permission, pending_context_close and cleanup_errors. They include canceled starts awaiting microphone permission, contexts awaiting close promises and sockets awaiting close events. Worklets remain counted until their owning context closes. A failed cleanup remains visible and blocks another start. These counts do not measure native OS resources or prove absence of a device leak. `cleanup` is pending/active/observed_released; physical_audio and calibration remain unknown.

Playback seconds estimate admitted context frames minus worklet-consumed frames; upload seconds conservatively divide serialized buffered bytes by PCM16/base64 bytes per second. Worklet quantum/queued_frames stay unknown until observed. Browser-only headline timing never uses clock alignment.

`drain(afterSeq=0, limit=500)` is non-destructive, limit 1..1000, ring capacity 1000. It returns events with seq/at/kind/allowlisted numeric metadata, cursor, latest_seq, oldest_seq, dropped_before_cursor and dropped_total. Advance using returned cursor and continue until latest_seq. Any nonzero gap is lost evidence, not success. No nonce, free text, transcripts, raw microphone PCM or live media/socket objects are returned. Manual capture remains ephemeral. Export downloads use this same safe metadata.

## Synthetic graph probe (final v1)

`diagnosticProbe({kind:'gap'|'offset'|'cancel', gap_ms?:number, offset_ms?:number, cancel_after_frames?:number})` requires mock mode, an explicitly started/admitted running graph, listening state or drained paused awaiting_response, and no active response/probe/queue. Drained admission additionally requires playbackDone and an observed empty worklet queue. It pauses microphone capture and keeps the visible microphone ownership indicator. Only an internally generated 440Hz, 0.08-amplitude synthetic signal enters the worklet output. No task commit/provider call is made. Bounds: gap_ms 20..1000 (default 100), offset_ms -250..250 (default 0), 128 probes per graph, IDs monotonic page-wide. Recreating the context advances graph_id; graph IDs are scoped to this page/document and must be joined with the driver's actual page identity.

Return shape:

```ts
{
  schema_version: 1,
  graph_id: number,
  probe_id: number,
  sample_id: string, // generated synthetic440-gN-pN; never input text
  sample_rate: number,
  requested_frame: number,
  pulse_frames: number, // ceil(actual context rate * .04)
  gap_frames: number,
  cancel_after_frames: number | null,
  intended_cutoff_frame: number | null,
  kind: 'gap' | 'offset' | 'cancel',
  observation: 'requested',
  boundary: 'worklet_render_graph',
  physical_audio: 'unknown', calibration: 'unknown'
}
```

requested_frame = round((context.currentTime + .1 + offset_ms/1000) * actual rate). Negative offset can deliberately request a past schedule. Actual rendering clamps its start to currentFrame; requested and actual anchors remain distinct.

Gap/offset: two 40ms pulses separated by gap_frames. Cancel: one 40ms pulse with a scheduled worklet sample fence. cancel_after_frames defaults to ceil(rate*.02), must be an integer 1..pulse_frames-1, and is rejected on other kinds. intended_cutoff_frame = requested_frame + cancel_after_frames. Before writing every graph sample, the worklet checks this absolute cutoff; at/after it, it emits probe_fence_applied and probe_canceled and writes zero. A past cutoff can produce no sound; null first/last frames remain unobserved. There is no main-thread timer offered as proof of intended cutoff.

`cancelDiagnosticProbe()` separately requests an explicit main-thread cancellation. It returns schema_version/graph_id/probe_id/intended_cutoff_frame/observation=requested/cancel_origin=explicit_main_thread. The intended frame is the main-thread context estimate; actual command receipt/fence is separately observed. It never claims sample-accurate main-thread scheduling.

`snapshot().last_probe` repeats the request metadata and adds terminal_kind, observation=observed_render_graph, observation_statuses and bounded events. Every observed event has kind, graph_id, probe_id, sample_id and applicable numeric anchors:

- probe_started: frame, requested_frame, pulse_frames, gap_frames, intended_cutoff_frame, quantum, sample_rate.
- probe_pulse: pulse index and actual non-silent output frame, plus graph/sample/request fields.
- probe_fence_applied: actual frame, last_old_frame, intended_cutoff_frame and cancel_origin (scheduled_worklet / explicit_main_thread / session_fence).
- probe_done / probe_canceled: frame, first_frame, last_frame, last_old_frame, non_silent_frames, quantum/sample_rate and request anchors.

Raw mappings on each observed event are output_timestamp_context_seconds, output_timestamp_performance_ms, output_estimate_ms and mapping_observation=estimated|unknown. Null is unobserved. Output estimates use the actual getOutputTimestamp mapping once and do not add outputLatency. The graph tap observes samples actually written into the output buffer; it is not physical loopback. Returned anchors do not compare thresholds, issue calibration acceptance or prove acoustic success. D7 owns repetitions/attempt denominators and activation-only mapping faults. A missing-completion timeout after three seconds closes the failed session without inventing output.

A probe records return_state at admission. Listening-entry probes return to listening without resetting their current wire transport ID/sequence. Drained awaiting_response-entry probes return to that paused state after observed terminal: no listen call, capture restart, turn allocation, input upload or task-sequence reset occurs. End/device/relay failure records last_probe as incomplete with partial observations, not a fabricated worklet terminal. No raw manual speech is persisted or replayed. Relay/task/silence deadlines remain active; drivers must retain interrupted controls as incomplete. The browser API does not silently waive those deadlines to complete a calibration run.

## Alignment and lifecycle wire

Frontend owns at most 20 canonical `{type:'clock_probe', probe_id, browser_sent_ms}` requests per fresh relay session. Each has a tracked 500ms timeout. The server's combined budget includes legacy ping and survives reconnects; D7 must observe this frontend series rather than add a second 20 requests. Matched `clock_pong` fields are probe_id/browser_sent_ms/server_received_ns/server_sent_ns/clock_domain=server_monotonic_ns/browser_clock_domain=browser_performance_ms/boot_id. Unsafe integer nanoseconds, mismatched boot identity, expired and duplicate pongs remain rejected/unknown.

Summary retains requested/completed/observed/missing and every RTT/offset/uncertainty sample. Only a complete 20-request attempt with minimum RTT <=60ms exposes an estimated midpoint offset. Symmetric-path +/-RTT/2 uncertainty is explicit; no one-way delay is inferred. Browser headline timing never uses alignment. On reconnect, pending requests expire and old-connection samples stay visible as stale diagnostics with null usable offset; no probe-budget reset or silent cohort pooling occurs. A changed boot identity cannot resume the prior session.

Client terminal observations use D4's exact `{type:'client_status', outcome}` only: permission_denied/device_lost/silence_timeout/abandoned/unsupported_audio. Mute is a local exact device_mute event; termination uses canonical device_lost. Preconnection failures remain local only; a dispatched status is not a receipt. Relay ended outcome/outcome_source/trace/evidence_reference are displayed separately from task/cost, with no invented adjudication. Committed task snapshots survive interruptions and are refreshed from relay state/task messages.

Every start owns its media/context/worklet/socket/listeners/timers. End invalidates its generation before cleanup; late grants are stopped and async stages cannot release a newer graph. End/failure also clears download RAF/object-URL scopes. Explicit reconnect uses 250ms then 1s backoff, at most two attempts in the original five-second window (including permission/graph acquisition), original task/response deadlines, no audio replay, and at most one repeat request. Capture waits for the post-resume listening decision. Transport turn IDs advance for a new attempted capture, including recovery before commit; a probe pause does not restart wire sequence. A fully drained response can start a next turn without truncating already heard audio. Permission/device/mute/silence failures require explicit user action.

R3 phase reconciliation: existing task/idle deadlines remain active. Full post-calibration controls must be coordinated with the relay phase/deadline producer; the frontend never creates an unscheduled keepalive speech turn or silently disables an idle deadline.
