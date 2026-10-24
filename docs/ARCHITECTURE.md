# Realtime Voice Lab architecture

Construction source, runtime unverified. Mock V, C and K are diagnostic pipelines;
they do not establish provider capability, measured latency or spoken-task success.

The browser captures actual-rate samples and converts to mono 24 kHz PCM16 before
WebSocket ingress. `voicelab.server.app:create_app` validates the loopback Host,
exact Origin, local launch nonce and strict protocol before session admission.
`contracts.py` is the canonical typed contract. `contracts/wire-v1.json` is a
human/tool-readable common-bound reference; it does not replace variant validation.

All server frames use `{type,protocol_version,session_id,connection_epoch,
response_epoch,sequence,payload}`. Each type has one closed payload model in the
`contracts.py` server payload registry, checked on the exact encoded frame before send.
Client frames are flat discriminated variants.
Identifiers contain letters, digits, `_` or `-`. Audio is base64 PCM16, mono
24000 Hz, at most 4800 samples/frame. Ingress has exact per-turn sequences,
15 seconds/turn, eight logical turns and 120 seconds/trial. Output backlog may not
exceed 48000 unacknowledged samples. Three sessions are admitted; a fourth is refused.

The adapter factory supplies the V/C/K mock implementations. Its wire parsers and
serializers sit below `ArmSession`; mock text is explicitly debug stimulus rather
than ASR. The adapter returns a diagnostic tone rather than licensed speech. The
default relay activation makes no external provider calls and admits only mock mode.
A programmatic live Settings path composes D2's guarded transport producer behind
explicit authorization/capability/pricing/rights/evaluation manifests and exact run
budget. Missing inputs refuse admission; no real provider capability is asserted.
The server serializes all output and records stale epoch discards before permitting
current audio. The live browser mode cannot override operator-side gates.

`TaskAuthority` owns SQLite transactions and every effect. It alone creates operation
IDs from namespace/logical-turn/command, stores original outcomes and appends every
attempt. A second uniqueness constraint allows only one booking per trial. Selecting
an offered slot creates a challenge bound to slot and state version; a separate
explicit affirmative turn with the current response epoch is required to commit.
The mock lexical controller supplies labeled mock affirmative evidence. Live native
confirmation requires an independently admitted trusted evidence verifier bound to
the actual input, current slot/version/challenge/epoch; a provider tool call or
transcript alone cannot supply it. Interrupting after commit preserves the booking.

The session reducer holds a response until generation finishes and the final rendered
sample is acknowledged. Interruption advances the epoch before adapter cancellation.
Old completions cannot pass the relay fence. On socket loss, the session is retained
for five seconds; reconnect uses the opaque session ID, same boundary and arm, followed
by a new connection epoch. Partial/missing playback ACK never replays audio. It yields
one explicit repeat request and a task-journal snapshot; a second ambiguous recovery
terminates. Browser reload has no recovered identity and abandons that trial. Process
restart cannot reconstruct in-memory sessions and is labeled service restart; SQLite
retains effects and liability.
Live provider recovery currently terminates `recovery_unknown` until its acknowledged
history rebuild can be established; the mock recovery path does not certify it.

Trace files contain bounded metadata-only `VoiceEvent` rows with explicit server
monotonic nanoseconds and a final SHA256. Raw PCM is not embedded in trace telemetry.
Diagnostic output tone frames are stored separately by SHA256 as mono 24 kHz PCM16;
their hashes appear in audio events. Microphone input is not retained by this mock
artifact path, so confirmation-audio custody and spoken-task adjudication remain unknown.
Live manual input/output is ephemeral; trace `content_hash` is a digest, distinct from
a saved `artifact_hash`. A trusted verifier must inspect actual ephemeral input rather
than infer affirmation from that digest. Optional per-trial synthetic retention
manifests bind explicit fixture/hash/rights/expiry approval to the actual trial ID.
Only the full-input hash match at closure admits raw persistence; optional output
retention includes rendered ACK prefixes only. No manifest/match remains ephemeral.
Per-session sidecars bind archived hashes to that manifest and custody expiry.
`AttemptLedger` separately stores exact Decimal reservations, settlement and unknown
liability; mock attempts reserve and settle zero with an explicit no-provider basis.
The model vocabulary also defines run manifests, turn results and evidence records;
no fixture seal or result record is fabricated by starting a session.

The relay now also journals lifecycle metadata in `task.sqlite3`: a process boot ID,
run/trial/session membership, each started transport turn and its stable logical turn,
first terminal outcomes, and approved artifact references. The single relay owner's
startup marks prior unterminated sessions/turns `service_restart` without rewriting
old trace files or declaring their missing terminal complete. Task effects, original
operation outcomes, duplicate attempts and accounting liability remain separate.
`GET /sessions/{session_id}/evidence` exports this metadata behind the same exact
Host/Origin/nonce boundary as diagnostics. It does not expose audio or transcripts.

A relay watchdog enforces 120 seconds per session, ten seconds from the original
server commit receipt for a response (including recovery), and eight seconds without
input while listening/capturing. The browser owns actual speech/no-speech observation
and the original speech-end deadline; server receipt is not speech end and clocks are
not subtracted across domains. A `client_status` frame reports a bounded client-side
permission/device/silence/abandonment/unsupported-audio outcome. It is labeled
`client_report`, never promoted to independently verified evidence or task success.
End/terminal shutdown advances both audio and durable-effect epochs before awaiting
cleanup. A response remains active until valid response completion plus rendered ACK;
PCM after completion, invalid identities, duplicates/reordering and overruns fail closed.

Trace rows reserve capacity for one explicit session terminal, retain integer
monotonic timestamps scoped to the boot ID, and fsync that terminal before marking it
durable. Observable controller spans keep their own explicit same-clock nanosecond
bounds rather than substituting provider-private timing. Relay `success` describes
the controller/render path or booked task state only; spoken correctness remains unknown.

Mock-only operator fault policy composes `core.faults.MockFaultController` into the
existing mock adapter. The HTTP control body has only a stable control ID and a closed
kind. Source receipts distinguish arm, actual injection and unresolved boundaries;
they retain seeded parameters, actual affected boundary and counts, never expected
behavior as measured acceptance. Fresh tool duplicates use both the existing mock
tool parser and durable authority, with distinct provider IDs and one logical operation.
The before-effect hook can only prevent a guarded mock confirmation, after F1/version/
epoch eligibility. Its retryable no-effect attempt does not replace an original operation.
After-effect drop occurs after the actual task transaction. Both recover without replay.
Simulated restart terminates only the target session with an explicit simulation source.

The terminal path saves one existing atomic run-ledger summary, including raw attempts
and events, as canonical hashed JSON; evidence GET verifies those saved bytes. A
separate saved fault snapshot retains adapter queue/discard/authority/effect counters.
Snapshot failures remain unknown. Clock probes only echo bounded browser values and
server receive/send observations, each with its clock/boundary/boot identity. No
one-way-delay arithmetic or actual process restart is inferred from these controls.

Accounting admission now stores typed per-attempt price/bound terms, trial and boot
owner beside the existing immutable attempt row. Reserve/dispatch policy checks,
three-attempt component limits and late over-bound usage serialize through the same
ledger. A bound violation halts even after unknown/settled terminals; it never refunds
unknown exposure or settles twice. Boot recovery retains unfinished exposure and
fences stale owner objects. Live enforcement/lease references are operator attestations;
the actual bound verifier/provider evidence is a separate unbuilt activation prerequisite.

Protected admission metadata fingerprints operator configuration without provider I/O.
Ready/session metadata separates requested/expected identities from returned identities
observed only through a future gated provider open. Injected transports, unreturned TTS/
voice identities and independent verification stay unknown. Boundary helpers reject
ambiguous loopback/Origin/nonce authorities and current filesystem link/traversal escapes;
operator-exclusive artifact ownership is required. Errors and accounting telemetry retain
fixed codes/numeric counters rather than raw provider content or request validation inputs.

Later validation must exercise full browser→relay→wire parser→authority→playback→ledger
journeys, cancellation, reconnect and cleanup, before offline evidence is admissible.
Licensed fixtures, calibration, independent heldout custody, approved provider/budget
manifests and independent live product acceptance remain separate gates.

## Conditional licensed K and integration source

The live K branch is a separate LicensedKSession on the same ArmSession/relay
capture, commit, authority snapshot, PCM, render, cancellation, reconnect and epoch
paths. It loads only an explicitly approved finite local pinned WAV catalog after a
real external approval callback. It reads authority state to select a fixed clip;
it submits no command or booking and supplies no speech/intent interpretation.
The F1 distinct-turn confirmation guard remains unchanged. A rendered control is
control_complete, separate from task success. Partial/unknown playback is never
replayed. One second of unacknowledged audio bounds its producer; the common relay
retains its independent queue/response/session bounds. It fabricates no model stage
or translation duration. No asset, actual approval callback or live activation is
provided. INTERFACES.md and contracts/licensed-control-v1.json describe the pins.

A protected bounded HTTP calibration phase pauses only idle silence admission for
local diagnostics, at most twice for30s each under the original120s session deadline.
It allocates no capture/logical turn or epoch, renews no logical response deadline,
and asserts no probe success. Active capture/commit/interrupt and disconnected
calibration refuse. Canonical phase snapshots survive End with hashes/references.
Exact attempts now retain actual session/trial/logical/transport/epoch lineage at
reservation; historical missing lineage remains unknown. Session evidence filters
only recorded exact matches from the unchanged saved run ledger snapshot and never
constructs a second total. These are source contracts, not execution observations.
