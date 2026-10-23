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
