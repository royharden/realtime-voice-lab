# Manual review workbench v1

This is source construction, not an executed review or product acceptance. The view is mounted below the existing session console. It has not loaded a packet/artifact, played audio, issued a judgment, or exported a real submission during construction.

## D5 contract and boundaries

`review-model.ts` consumes the current canonical models in `evaluation/adjudication.py` and the Manual-review consumer schema in `docs/EVALUATION.md`. Packet fields include schema_version/kind/packet_id/packet_hash/run_id/task_id/created_at/source_hashes/artifact_root_policy/items/evidence_status/split/product_acceptance/gates. Item count1..8, exact bindings and the six canonical roles input_audio/rendered_audio/confirmation_audio/confirmation/journal/trace are preserved. Optional explicitly selected ledger bytes compare to bindings.ledger_hash without inventing a new packet role.

D5 alone owns authoritative packet validation, append-only decisions, actor/appointment verification, journal head/supersession validation, custody release and oracle export. Browser checks establish local content/shape compatibility only. Neither a digest, checkbox, appointment name nor a JSON declaration establishes independence or rights authority. Heldout packets are refused before any artifact access; this view has no file flag or author-controlled release verifier to unlock them.

For producer v1, browser content hashing uses sorted Unicode code-point keys, compact JSON, Python ensure_ascii string escaping, and LF, excluding packet_hash. Producer v1 packet values contain integer schema fields and string/boolean/null gate metadata. Unsafe integers, float/exponent number representations and excessive nesting are refused rather than silently normalized into a different canonical digest. Future numeric schema changes require D5-owned reconciliation; this is not a second canonical verifier.

## Explicit local intake

Select one D5 dev packet and enter its externally supplied packet_hash pin. Select every artifact separately by role; no directory enumeration, path-based read, fetch, upload, provider call or storage API is used. Paths are displayed/screened as safe relative references; exact selected file bytes, role and binding hashes govern intake. Browser File inputs do not prove filesystem ancestry. The operator acknowledges that files came from the separately authorized owned root; D5's actual root/symlink checks remain external.

Per-file limits: packet512KB, each canonical playback declaration128KB, audio32MB, other evidence8MB, total retained/in-flight selected file bytes96MB. Async reads/hash work retain generation ownership; replacing/clearing a packet cannot admit stale results. A hash mismatch is displayed and its bytes are not retained. JSON/JSONL evidence must parse before admission. Trace intake is bounded to20,000 records and the inspector paginates100 records, filtering only by the exact observed session binding rather than rewriting planned/raw identities. Journal session selection uses the same actual bound ID. Depth/array/text/display omissions and parse/scope mismatches remain visible gaps that block positive export.

Audio playback uses D5's canonical **PlaybackDeclaration**, separately selected per item/role through the multiple-file picker. Each declared hash is checked before admission using the exact D5 top-level declaration_hash exclusion / recursive codepoint sort / compact ensure_ascii JSON+LF digest. The external declaration_hash pin is entered in that audio role's card.

```ts
{
  schema_version: 1,
  kind: 'manual_playback_declaration',
  packet_hash, item_id,
  role: 'input_audio' | 'rendered_audio' | 'confirmation_audio',
  audio_hash,
  container: 'wav' | 'raw_pcm',
  media_type: 'audio/wav' | 'application/octet-stream',
  encoding: 'signed_little_endian_pcm16',
  sample_rate: number, // safe integer 8000..192000
  channels: 1 | 2,
  bits_per_sample: 16,
  approved_synthetic: true,
  policy: 'operator_custody_expiry',
  approval_reference,
  input_rights_reference: string | null,
  output_rights_reference: string | null,
  manifest_reference, custody_reference,
  retention_until_utc: '<timezone-qualified expiry>',
  declaration_status: 'unverified',
  product_acceptance: false,
  declaration_hash
}
```

The role-specific input/output rights reference must be present, as in D5's model. This declaration carries externally supplied retention names; the view never invents their authority. Retention must be current and at most30days ahead on the browser clock. Packet, item, role, original audio hash, declaration hash and external pin must agree. Null/mismatched/expired references cannot admit playback. D5 owns its canonical validate_playback_declaration and later authority/retention gates; local shape/hash matching is not a custody release or approval.

WAV original bytes must contain PCM16 and match declared actual sample rate, channels1|2, block alignment, byte rate and data length. Bound raw PCM uses the declared actual rate/channels and frame alignment; a volatile WAV wrapper preserves that declared rate without treating the wrapper as a new source artifact. Duration bound120seconds. Other encodings/media/container combinations are refused. There is no audio/L16 big-endian relabeling. Explicit Play is required and waits for the main session handles to close. Local `ended` does not prove hearing, acoustic latency or correctness; physical sound remains unknown. Expiry stops playback.

## Human submission

All spoken/task/confirmation choices start unselected. Unknown is a deliberate valid human choice, never automatically changed into correct. Reasons are D5's exact closed15-code enum; select1..8. Actor and appointment references and human timezone-qualified decided_at are entered explicitly. The human checkbox declares authorship of the choices while keeping verification external. Positive choices cannot export with missing/mismatched/uninspectable evidence or expired declarations; completed local input/rendered playback is required for a positive spoken choice, and confirmation audio for positive confirmation. These UI prerequisites do not themselves prove review or correctness; D5 still evaluates all actual bindings and external authority.

The only output is a download named `UNVERIFIED-review-...json` with the exact canonical submission shape:

```ts
{
  schema_version: 1, packet_hash, item_id, actor_ref, appointment_ref,
  decided_at,
  judgments: {spoken:'correct|incorrect|unknown', task:'correct|incorrect|unknown', confirmation:'correct|incorrect|unknown'},
  reasons: ['<closed D5 enum>'],
  task_terminal: null,
  supersedes: '<prior exact record hash>' | null
}
```

No verified-actor flag, journal sequence/record hash, oracle method, product acceptance, audio, provider transcript/text, nonce, packet bodies or telemetry is added to the submission. Corrections name supersedes; D5 must check actual current history and a separately retained journal head. The browser neither overwrites old decisions nor authors heldout labels. Coding actors cannot supply actual independent W7/W8 decisions.

## Lifetimes and deferred activation

Clear local bundle or pagehide removes selected File/ArrayBuffer references, parsed packet/declarations, drafts, inspector content and audio. Pending reads/play promises stay counted until settling and cannot revive old state. Stop/item-switch/End stop and detach media, revoke owned object URLs and clear expiry timers; download RAF/object-URL ownership is visible and released on completion/clear. No AudioContext or microphone is created by this view. Session Start waits for a canceled review play promise to settle before acquiring its microphone.

`window.__voiceLabDiagnostics.snapshot().review_workbench_handles` exposes counts/byte bounds only, not bundle content or human references. Main session End stops review audio but leaves an explicitly selected approved bundle available for continued inspection until Clear/pagehide; session and review handles are reported separately. Counts describe owned browser references/API state, not heap wiping or OS/device resource certification.

Unexecuted prerequisites: semantic TypeScript check, browser/file/audio/cleanup/UI journeys; approved synthetic artifacts and genuine rights/retention references; externally appointed nonauthor reviewer and independently operated custody/authority; D5 canonical validation/history/oracle import and actual product acceptance. No fixture corpus, appointment, approval or review dataset is synthesized here.
