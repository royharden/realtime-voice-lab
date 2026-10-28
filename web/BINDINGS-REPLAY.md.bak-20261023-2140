# Generated browser bindings and saved replay inspection

Source construction only; no browser, provider, audio, report-reproduction workflow or product acceptance has run here.

## Canonical generation

`tools/generate_bindings.py` uses Python stdlib AST parsing only. It reads exact bytes of `src/voicelab/contracts.py`, `evaluation/adjudication.py` and `evaluation/reproduction.py`, resolves annotated aliases/models/inheritance and static Field constraints, and emits `web/src/generated/contracts.ts`. It never imports those modules, Pydantic, the app, database, adapters or provider code. Unsupported annotations/references or a changed canonical closed-review-role validator fail visibly instead of inventing types.

The generated source contains client/server envelope and VoiceEvent types, Identifier/Epoch/Hash and closed outcome/message/event/reason literals, D5 review/playback/submission models, sealed reproduction inputs/pins, model field inventories, structural rules, exact source SHA256 pins and unexecuted custom-validator names. No defaults are inserted into user evidence before content hashing. Generation is deterministic, UTF8/LF, with no invocation time, data fixtures or authority labels.

From the OUTER shell, source generation/check commands are:

```powershell
python realtime-voice-lab/web/tools/generate_bindings.py
python realtime-voice-lab/web/tools/generate_bindings.py --check
```

The second command only compares generated source to canonical source bytes. Future package generate:bindings/check:bindings and precheck/pretypecheck/prebuild hooks use the same script. No npm/build/typecheck command was run during construction; no dependencies were installed. These hooks do not constitute CI or infrastructure scaffolding.

`boundary.ts` consumes generated metadata as bounded unknown-data structural guards and typed client/server boundaries. It enforces closed model fields/literals, required inputs, source field bounds/patterns and safe integer IDs/epochs. Main source retains PCM/base64/sample bounds, stale epoch/sequence/render ACK fences, actual server-payload checks and named capability/error behavior. D5 review types/reasons/field inventories now import generated definitions rather than separate hand-maintained DTOs.

Custom Python validators and cross-field/authority semantics are explicitly not translated or run by this guard. Current canonical ServerMessage.payload is dict[str,object]; the browser does not invent an authoritative typed audio/tool payload model. Those values stay unknown until the existing application checks them. Generated TS types do not prove runtime compatibility, provider entitlement, safe clock arithmetic or independent review. A future canonical payload schema export belongs to its producer and requires regeneration/reconciliation. Raw driver observations likewise remain unknown saved metadata unless a canonical model actually exists.

## Saved local replay view

The Run and trace inspector is mounted below the canonical manual-review view. It reads only a user-selected D5 ReproductionExpectation and requires the independently supplied exact expectation-file SHA256 plus explicit approved synthetic owned-root declaration before saved-file intake. Heldout expectations/manifests stay closed; file selection cannot establish filesystem ancestry, custody or independent rights authority.

Every input/expected-output reference is displayed including explicit null/missing inputs, relative path, kind and hash. Each file is selected individually, SHA256 checked against original bytes and bounded (32MB/file, 96MB retained/in-flight file bytes, metadata expectation512KB). No directory discovery, remote fetch/upload, microphone, WebSocket, provider, task mutation, report execution or file writing exists in this view. Expected outputs are inspected as saved data only; there is no regeneration, equality acceptance or new reporter/oracle.

Manifest and original-manifest bytes must match sealed pins. The view presents raw saved trace/turn/attempt/correlation collections with original array/file order and file index, planned and observed IDs in separate columns, closed structural issues, saved outcomes/failures/unknowns and explicitly supplied censoring labels. Null is unknown rather than inferred censoring. Raw JSONL source text remains exact; parsed JSON views flag unsafe integer precision and an explicit source-character window exposes the original saved text. No browser/server/provider clock subtraction, percentile, cost summation, attempt deduplication or correctness inference is performed. Provided-row counts are not scheduled/started denominators.

Collections include original correlations, attempt_correlations, task admissions, ledger events/attempts and transport-attempt children under their original parent names. Current structural-model mismatch never silently drops a record or overwrites observed IDs with planned aliases. Capability errors and missing terminals stay visible as saved facts/unknowns; declared success is not independent product acceptance. Bounded display cannot certify that an operator inspected all supporting evidence.

Approved audio is deliberately reused through the existing D5 ReviewPacket/PlaybackDeclaration workbench. The selected review item must match the saved run/code/build/fixture/plan and exact original-manifest source hash, and original manifest bytes must be selected/hash matched. The link focuses the existing audio controls; it never autoplays or alters judgments. Explicit Play still requires original audio hash, canonical per-role declaration hash/external pin, rights/retention metadata, idle live-session resources and the ordinary review cleanup controls. No second audio authorization or oracle is introduced.

Clear/pagehide releases saved File/ArrayBuffer/text references. Generation ownership and pending read/hash counts prevent stale intake after a selection is replaced. `snapshot().replay_inspector_handles` exposes counts/byte bounds only, never source text, transcripts, human references or nonce. Browser references are not an OS/heap wiping claim.

## Deferred activation

Semantic TypeScript/no-emit verification is unavailable without existing compiler dependencies. Browser/file/codec/cleanup/visual journeys, malformed/future schema cases, full generated semantic equivalence and saved/live provider behavior remain unverified. Actual approved artifacts, genuine rights/custody/independent appointments and D5 canonical authority/journal/oracle validation remain external prerequisites. The view and the static generator issue none of those gates.
