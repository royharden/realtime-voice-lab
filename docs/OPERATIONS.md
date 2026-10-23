# Local operation and deferred activation

The source was constructed in Dirty Mode. No dependency resolution, installation,
server/browser/database start, test, calibration or measurement has been performed.
The instructions below are for a later authorized activation, after Dirty Mode closes.

Claim and check a localhost port with the shared agent-ports registry before serving.
The CLI requires an explicit port in 20000–29999 and an existing port-claim receipt.
Use `voicelab serve --help` for exact arguments. The factory is
`voicelab.server.app:create_app`; host binding is 127.0.0.1. Keep frontend and relay
on separately claimed ports. Set `VOICELAB_ALLOWED_ORIGIN` to the frontend's exact
loopback HTTP origin, including its claimed port. There is no implicit origin or
port. Missing configuration and wildcard origins are refused before activation.

Set `VOICELAB_NONCE` to an operator-created random 16–128 character local launch nonce.
Enter the same value in the UI. It is a local cross-origin request guard, not a provider
key or account credential. Never put real provider credentials in this app. WebSocket
URL `/ws?nonce=...` and hello both carry the nonce. Do not retain full access-log URLs
or share URLs containing it. The relay uses generic boundary errors without echoing it.
No endpoint serves the nonce. `/health` reports only diagnostic evidence labels.

## Conditional future live source activation

The CLI/environment factory stays mock by default. A separate, later authorized
operator may construct the programmatic `Settings` object and pass it to
`create_app(settings)`: `mode="live"`, `nonce`, `allowed_origin`, `artifact_root`,
`enabled_arms=("V", "C")` (or a strict subset), and an explicit `live_config`.
The browser's live selection sends only `hello.mode="live"`; it cannot supply or
override authorization, prices, provider models, transports or confirmation evidence.
Operator mode and client mode must match. Live commits containing debug `text` are
refused. No live startup, key read or network call was performed during construction.

`voicelab.adapters.live.validate_live_config(config, arm)` is the pure producer gate.
The relay calls it at factory construction and again at admission, then creates the
run ledger with its exact normalized `budget_usd`. It injects normalized configuration
into the same `create_arm`/`ArmSession` composition as mock, including current
connection/response epochs and stable logical-turn identity. Each live run uses its
own ledger file, shared by its admitted sessions. The trace uses the supplied run ID
and labels provider transcripts as unverified. Live output PCM is ephemeral and
receives a content digest, not a saved-artifact claim or synthetic label.

Live input PCM frames remain ephemeral. The relay computes SHA256 of actual bytes
and passes `input_artifact_hash` with `input_artifact_persisted=False` to the adapter;
that producer field is an ephemeral digest, not a saved-file reference or a
client-supplied claim. The trusted verifier must inspect available ephemeral input
independently; a hash alone cannot prove an affirmative confirmation. Missing input
evidence fails closed. Mock microphone input likewise remains unretained.

An optional dormant `Settings.retention_manifests` tuple supplies approved synthetic
trial assignments. Each entry has exactly these fields: `arm`, `run_id`, `trial_id`,
`fixture_id`, `synthetic` (literal true), `approved_input_sha256` (1–32 distinct lowercase
SHA256 values), `retain_input`, `retain_output`, `approval_reference`,
`input_rights_reference`, `output_rights_reference`, `manifest_reference`,
`retention_until_utc`, `custody_reference`, and `policy="operator_custody_expiry"`.
IDs use the canonical identifier grammar; references are nonempty bounded strings.
The expiry must be timezone-aware, in the future and within 30 days. Output rights
are required when `retain_output` is true. No approval or fixture is supplied by the app.

Assignments are consumed once per factory in arm order, binding the actual server
trial namespace to the approved `trial_id`; run and arm must match operator live
configuration. Buffering is bounded to eight 15-second input turns and eight 10-second
output turns of PCM24k mono. Only after admission closes does the relay hash the entire
trial's concatenated canonical input PCM (not WAV headers or a partial prefix). Raw
input is persisted only on an exact unexpired allowlist match and `retain_input`.
Output additionally requires `retain_output` and output-rights approval, and retains
only the PCM prefixes actually acknowledged rendered for each response. Received,
queued, canceled-unplayed output is excluded. If no policy or full-input match exists,
raw data is discarded; no transcript artifacts are written.

Matched files live under `approved-synthetic-audio` by content hash, with a per-session
JSON sidecar recording trial/fixture identity, hashes, permissions, approval/rights and
manifest references, and custody expiry. The named custodian must remove retained
artifacts by that explicit expiry; this policy does not install a watcher or background
retention service. Archived audio cannot claim physical acoustic measurement. Before
finalization, frame digests and verifier inputs remain ephemeral even for an approved
trial. A failed retention write is reported unknown, never as successful custody.

The live producer requires explicit authorization, capability, pricing, rights and
evaluation manifests; exact model/voice selections; approved budget; and later
runtime credential/transport providers. Missing, expired, unsupported or conflicting
inputs refuse admission before provider dispatch. See `docs/PROVIDERS.md` for the
producer's exact manifest fields and allowlisted endpoints. No model or TTS choice is
certified by this source. K live admission is closed until a licensed canned-audio
control is explicitly supplied; the mock tone is not a substitute.

Native confirmation remains fail-closed without an operator-injected trusted
confirmation-evidence verifier. It must independently bind the actual affirmative
input/evidence reference to the trial, logical/transport turn, current response epoch,
slot, challenge and state version. A native tool proposal or provider transcript
cannot establish this evidence. The verifier must not send heldout labels or oracle
answers to the provider. Missing verifier/evidence is a source activation prerequisite,
not merely a skipped runtime test; provider conversation cannot safely book without it.

Actual activation remains blocked pending supported provider/TTS decisions and
capabilities, refreshed dimensioned prices/bounds and approved budget, licensed
speech/control rights, offline calibration/fault/soak evidence, independent custody
and reviewer appointment, trusted affirmative-input verification, and explicit later
operator authorization. Source wiring does not satisfy those decisions or M6 live
acceptance. Do not create example approvals to bypass the gate.

Live reconnect additionally refuses `recovery_unknown` until provider history
rebuild/acknowledged cutoff behavior is independently verified. The mock recovery
mechanism below is not evidence that a live provider conversation can be restored.

`GET /diagnostics` uses the same loopback Host/exact Origin boundary and the
`X-Voicelab-Nonce` header. It returns only the current server process's RSS bytes,
open socket/file counts, current-event-loop task count and a single 10 ms scheduled
wake-delay sample. Each metric has `value`, `observation`, `unit` and an optional
reason. Optional psutil absence/platform limits produce unknown rather than zero.
No file paths, socket addresses, task names or other-process metrics are returned.
The later soak driver samples every two seconds; this endpoint does not establish
that the required soak or cleanup checks passed.

## Lifecycle and metadata protocol

The existing protocol version remains 1. The canonical typed parser accepts an
additional exact client variant, with no free-text reason field:

```json
{"type":"client_status","outcome":"device_lost"}
```

Allowed outcomes are `permission_denied`, `device_lost`, `silence_timeout`,
`abandoned`, and `unsupported_audio`. A session established after a permission/device
failure can receive this observation; a failure before connection stays a browser
observation and must not be claimed as a server-observed event. Explicit user retry
starts a fresh capture/session; no failure automatically restarts microphone use.

Ready frames add `run_id`, `trial_id`, `boot_id`, and `evidence_reference`. Ended
frames preserve their old fields and add `outcome`, `outcome_source`, `trace`,
`lifecycle_persisted`, `evidence_reference`, and unknown `spoken_correctness`.
Errors include a named terminal outcome. Queue bounds map to `queue_overrun`, budget
refusal to `budget_halt`, invalid protocol/identity/sequence to `protocol_error`,
response timeout to `provider_timeout`, and exhausted ambiguous recovery to
`recovery_unknown`. Ending a booked task can report session `success` without claiming
the pending audio rendered or spoken correctness; an unfinished transport turn is
`abandoned`. Interrupted transport attempts are recorded `expected_interruption`.

The relay watchdog uses commit receipt on its own monotonic clock, never a browser
speech-end timestamp. Recovery retains the original logical response deadline; its
new transport turn does not reset that deadline. The browser must additionally enforce
the speech-end boundary and detect actual silence/mute, which continuous silent PCM
alone cannot establish at the relay. A reconnect claims a disconnected socket once,
does not extend the original five-second window, and never replays old audio.

After later activation, request `GET /sessions/{session_id}/evidence` with exact
Origin and `X-Voicelab-Nonce` headers. It returns session/run/trial/boot metadata,
started transport-turn identities and terminal states, artifact hashes/relative
references, and the durable task's original operations plus duplicate attempts.
The task-attempt export explicitly reports count, limit and truncation at 256 rows;
SQLite retains the complete journal. This endpoint accepts an ID for SQL lookup,
not an artifact path, and returns no raw audio/transcript/key. Runtime authorization
and atomic durability still require later verification. Use one relay owner per
artifact root: startup classifies unfinished prior-owner sessions `service_restart`,
preserving task effects, accounting liability and original outcomes. It leaves old
traces untouched and their missing terminals unknown. A reported lifecycle write
failure or missing fsynced trace terminal cannot be treated as complete evidence.
`GET /sessions/{session_id}/trace` uses the same boundary after terminal closure,
returns at most 32 MB of metadata-only JSONL, and reports its actual SHA256 plus
`X-Voicelab-Trace-Complete`. It refuses a stored-hash mismatch. A reconciled crash
can export an incomplete trace but cannot turn that prefix into a terminal-complete
measurement. The known session selects a registered relative reference; arbitrary
paths are never accepted. Programmatic mock operators may set `Settings.run_id`
and an optional `run_manifest_hash` to link a saved run; these are syntax-validated
operator claims. The default remains `mock-local`; actual observed trial/session
IDs stay distinct from planned IDs and require the saved mapping in INTERFACES.md.

## Operator-only mock fault source

Faults are disabled by default. A later authorized mock operator may supply
`Settings.mock_fault_policy` with literal `enabled=true`, a fixed u32 `seed`, a real
bounded `authorization_reference`, and optional `max_controls` 1..32. No sample
authorization, provider payload, URL/path, executable code or policy comes from the
browser. Supplying fault policy in live mode refuses factory activation; the endpoint
also checks mode/policy and the ordinary exact Host/Origin/nonce boundary.

POST a maximum 512-byte JSON body to `/sessions/{session_id}/faults`:

```json
{"control_id":"dev-fault-001","kind":"fresh_tool_duplicate"}
```

The closed kinds and canonical receipt are in contracts.py and
contracts/fault-controls-v1.json. Same ID/kind while active returns the frozen first
HTTP receipt without injecting again; changed kind with that ID, capacity excess,
same pending kind, expired or ended target refuse. An armed receipt is not an applied
fault. Read current control diagnostics through active evidence, and saved receipts
through post-End evidence. Late/reordered/duplicate PCM retains sequence and epoch
identity; missing audio/transcript suppresses parsed output. Queue-overrun requests
inject 12 bounded 200 ms tone frames; the later observed terminal may differ from the
requested queue outcome and must not be relabeled as a successful fault test.

`lost_ack` drops the next valid rendered ACK before changing relay playback state.
Before/after-commit drops apply only at an actually eligible confirmation effect:
the former records a retryable no-effect attempt, the latter follows SQLite commit.
They close only the target mock socket and use normal bounded reconnect, journal
query and repeat identity. Fresh-tool duplicate uses new mock tool/provider-transport
IDs with the same logical operation; the authority preserves original outcomes and
one booking. Counts in the authority journal and fault snapshot are evidence inputs,
not independent task/adjudication verdicts.

`service_restart` simulates only a target-session termination, with
`outcome_source=mock_fault_simulation` and `actual_process_restart=false`; it never
terminates/restarts an OS process. `provider_error` passes a generated error through
the existing mock/native error parser before relay termination. Fault boundaries not
reached before End become unresolved; new controls after End refuse. Post-End
evidence remains readable with the same local boundary.

Terminal evidence now includes `ledger` plus `ledger_artifact`, a canonical JSON
snapshot with raw attempts/events/totals, SHA256 and relative reference. It is the
existing atomic run-ledger read, saved once for that ending session; it can contain
other sessions' attempts/reservations. Later GET verifies the saved snapshot instead
of recomputing a newer bill. `faults`/`faults_artifact` similarly retain receipts and
owned mock queue/discard/authority-outcome/effect counters. Unavailable or oversized
snapshots stay null/unknown, never empty zero-cost success. No accounting source changed.

The additional client `clock_probe` variant has `probe_id` and finite nonnegative
`browser_sent_ms`; `clock_pong` echoes them with `server_received_ns`,
`server_sent_ns`, explicit clock/boundary/boot labels and no one-way-delay calculation.
The combined clock-probe/legacy-ping allowance is 20 per session. Duplicate probe
IDs or exhausted allowance end with a protocol error. The browser may calculate a
bounded clock diagnostic from saved observations, but server receipt/send are not
browser receipt, speech end or physical audio output. All these paths remain unrun.

## Accounting and boundary completion source

The late-bound fix applies to reserved, settled and terminal-unknown attempts. An
authoritative amount exceeding its reservation atomically records invalid-bound
evidence and halts new reservations/dispatch. Unknown exposure and original settlements
remain immutable; duplicate/late evidence cannot charge or refund twice. The ledger's
old total is still known settlements plus held reservations, not a complete external
bill after a bound is disproved. Check admission_halted, bound_integrity and bound
violation counts; a null dispatch_allowed is not an affordability grant.

Billable reservations require canonical ReservationTerms with explicitly supplied
known per-unit USD rates, matching maximum-usage/unit dimensions, model/version/basis
and pricing/capability/enforcement references. The code supplies no tariff. Bounds
must cover the exact declared maximum; discrete units must be integers. Supported
amount strings have at most 18 integer and 18 fractional digits; unsupported actual
amounts halt as unknown instead of rounding or accepting an unsafe bill. Missing usage
stays unknown, and only priced numeric counters enter events. Raw text/audio/keys are
not saved as usage. A component can reserve at most three attempts per trial/logical
turn/arm, including canceled/unknown retries.

The existing reserve/settle APIs remain with additive source methods/metadata.
Live reserve adds terms/trial/boot owner; mock-zero-v1 remains the keyless zero-cost
case. authorize_dispatch and ensure_dispatch_allowed check the persisted halt/owner
and active reservation before billable I/O. A grant is not remote execution or charge
proof. record_sent means the transport send returned; failures before a returned send
remain unknown about remote charge. A future sole artifact-root startup may explicitly
recover the new boot owner; unfinished prior reservations become service_restart/
unknown, with exposure held. Ordinary per-session constructors cannot steal ownership
back. Budget mismatch refuses and never rewrites journals or resets a halt.

Actual unbuilt live activation prerequisites include a synchronous injected
attempt_bound_verifier that verifies the supplied terms against real provider/session/
history controls and lease context, plus real dimensioned tariff/usage_bounds/
usage_units/enforcement_reference and capability/owner evidence. No permissive
verifier or approved example is installed. The existing supported TTS, licensed K,
rights/custody/calibration, prices/budget and credentials remain separate prerequisites.
The new source contracts do not guarantee any external bill. Runtime refusal,
concurrency, crash atomicity and error-before/after-charge behavior remain untested.

GET /admission uses the exact Host/Origin/X-Voicelab-Nonce boundary and returns per-arm
AdmissionEvidence. Ready.payload.admission and saved session admission use the same
typed shape; see contracts/INTERFACES.md and admission-evidence-v1.json. It exposes
mode/run/run-manifest links, manifest fingerprints, requested/expected/actually-returned
identities, budget-owner scope/boot and lease reference. Hashes and lease references
are operator attestations, not independent verification. Metadata GET never contacts a
provider. Before future open, actually-returned identities are unknown; only default
provider transport session.updated observations can populate returned models. Injected
transports and unreturned TTS/voice fields remain unknown. No raw policy or credential
is included.

Boundary Origin must itself be exact loopback HTTP with an explicit port; Host must
be one unambiguous ASCII loopback authority. Nonce is 16..128 URL-safe ASCII characters.
Duplicate boundary headers or query nonces refuse. HTTP errors use fixed structured
detail.code/error.code without echoing validation inputs. Artifact root/ancestors and
children cannot currently be symlinks/junctions, linked files, traversal, drives/UNC,
ADS or reserved-name escapes. Keep the root under one operator with no competing
filesystem writer; these source checks do not prove race-free filesystem replacement.
The later listener must additionally bound transport frames to the application limit
and suppress/redact URL access logging, because the WebSocket query carries its local
nonce. Those root-listener settings are owned by the Integrator; no listener ran here.

`VOICELAB_ARTIFACT_ROOT` selects local runtime artifacts (default `.agent-work/runtime`
relative to activation cwd). Startup creates task/attempt SQLite files and bounded
metadata traces there. Use an operator-controlled directory, outside public exports;
do not point it at a symlink, existing sensitive data or another project's directory.
No artifact write happens merely by importing source. End closes adapter/task/trace
ownership. Keep journals for effect and exposure reconciliation; do not reset them
to make unknown liability disappear.

Mock commands include hours, slots, select slot 1/2, explicit confirm, and status;
unknown wording asks clarification. Audio-only mock input cannot infer intent and
must remain labeled. Tone output cannot pass spoken-answer correctness. Cancellation
preserves already committed task effects. The trace inspector distinguishes receipt
from estimated render timing and never establishes physical sound at the ear.

For reconnect, retain ready.session_id and retry after 250 ms then 1000 ms, no more
than twice or five seconds total. Send hello to `/ws?nonce=...&session_id=...`, then
`resume` with connection_epoch+1 and last actually rendered ACK, if known. Server
advances response_epoch and returns the journal. Do not ACK received/scheduled audio
as rendered. Unknown/partial playback produces at most one explicit repeat request;
no audio is replayed. Retry cannot silently grant microphone capture. End or reload
abandons rather than inventing continuity.

Deferred checks: resolve pinned dependencies and locks; run Python/TypeScript schema
checks and task/accounting/security tests; exercise UI, wire faults, fourth-session
refusal, stale cancellation, five-second reconnect and cleanup; calibrate actual audio
hardware and run bounded soak; obtain licensed speech and independent custody; refresh
capabilities/prices and get explicit live budget authorization; perform independent
product acceptance. Until then all runtime behavior and measured outcomes are unknown.
