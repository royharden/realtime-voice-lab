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
