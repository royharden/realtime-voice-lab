# Realtime Voice Lab architecture

Construction source, runtime unverified. Mock V, C and K are diagnostic pipelines;
they do not establish provider capability, measured latency or spoken-task success.

The browser captures actual-rate samples and converts to mono 24 kHz PCM16 before
WebSocket ingress. `voicelab.server.app:create_app` validates the loopback Host,
exact Origin, local launch nonce and strict protocol before session admission.
`contracts.py` is the canonical typed contract. `contracts/wire-v1.json` is a
human/tool-readable common-bound reference; it does not replace variant validation.

All server frames use `{type,protocol_version,session_id,connection_epoch,
response_epoch,sequence,payload}`. Client frames are flat discriminated variants.
Identifiers contain letters, digits, `_` or `-`. Audio is base64 PCM16, mono
24000 Hz, at most 4800 samples/frame. Ingress has exact per-turn sequences,
15 seconds/turn, eight logical turns and 120 seconds/trial. Output backlog may not
exceed 48000 unacknowledged samples. Three sessions are admitted; a fourth is refused.

The adapter factory supplies the V/C/K mock implementations. Its wire parsers and
serializers sit below `ArmSession`; mock text is explicitly debug stimulus rather
than ASR. The adapter returns a diagnostic tone rather than licensed speech. The
relay does not make external provider calls or admit live mode. The server serializes
all output and records stale epoch discards before permitting current audio.

`TaskAuthority` owns SQLite transactions and every effect. It alone creates operation
IDs from namespace/logical-turn/command, stores original outcomes and appends every
attempt. A second uniqueness constraint allows only one booking per trial. Selecting
an offered slot creates a challenge bound to slot and state version; a separate
explicit affirmative turn with the current response epoch is required to commit.
The mock lexical controller supplies that affirmative evidence; a future provider
tool call alone must never do so. Interrupting after commit preserves the booking.

The session reducer holds a response until generation finishes and the final rendered
sample is acknowledged. Interruption advances the epoch before adapter cancellation.
Old completions cannot pass the relay fence. On socket loss, the session is retained
for five seconds; reconnect uses the opaque session ID, same boundary and arm, followed
by a new connection epoch. Partial/missing playback ACK never replays audio. It yields
one explicit repeat request and a task-journal snapshot; a second ambiguous recovery
terminates. Browser reload has no recovered identity and abandons that trial. Process
restart cannot reconstruct in-memory sessions and is labeled service restart; SQLite
retains effects and liability.

Trace files contain bounded metadata-only `VoiceEvent` rows with explicit server
monotonic nanoseconds and a final SHA256. Raw PCM is not embedded in trace telemetry.
Diagnostic output tone frames are stored separately by SHA256 as mono 24 kHz PCM16;
their hashes appear in audio events. Microphone input is not retained by this mock
artifact path, so confirmation-audio custody and spoken-task adjudication remain unknown.
`AttemptLedger` separately stores exact Decimal reservations, settlement and unknown
liability; mock attempts reserve and settle zero with an explicit no-provider basis.
The model vocabulary also defines run manifests, turn results and evidence records;
no fixture seal or result record is fabricated by starting a session.

Later validation must exercise full browser→relay→wire parser→authority→playback→ledger
journeys, cancellation, reconnect and cleanup, before offline evidence is admissible.
Licensed fixtures, calibration, independent heldout custody, approved provider/budget
manifests and independent live product acceptance remain separate gates.
