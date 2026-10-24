# Reviewed development fixture catalog source

`voicelab.harness.catalog` validates and imports explicitly named development assets. Source construction only: no catalog, audio, private text, voice generation, import, browser or provider operation ran while this module was built. It does not certify speech, rights, custody, a returned provider identity or product readiness. D5's external authorization/custody/rights/build-freeze gates remain authoritative.

The existing diagnostic-tone fixture schema remains separate. A reviewed catalog never gives a single diagnostic WAV new family/speaker labels to satisfy a paired cohort.

## Closed schema

The catalog object has exactly `schema_version:1`, `catalog_id`, `split:"dev"`, `run_id`, `trial_plan_hash`, and `fixtures`. IDs use 1–96 ASCII letters, digits, underscores or hyphens. The plan hash is the canonical frozen D5 plan hash; this module checks its binding, not AB/BA balance or gate authenticity. There is no heldout-label or release switch.

Manifest files are bounded UTF-8 JSON. Duplicate keys and nonfinite numbers are refused rather than interpreted with last-value-wins behavior. A catalog contains at most 144 uniquely bound tasks; it never silently clips a larger plan.

Each fixture row has exactly these fields:

- `fixture_id`, `wording_family`, `speaker`, `task_template_id`: fixed identities, with no wildcard bindings.
- `synthetic:true`, `speech:true`: explicit reviewed declarations, not content analysis or self-issued approval.
- `generator`, `generator_version`, `voice`, `settings`: actual pinned provenance; no built-in/default provider or voice. Settings are bounded JSON metadata and reject credential, raw text/audio and request-header fields. Version pins may contain normal version punctuation.
- `generator_reference`, `voice_reference`, `provenance_reference`: each an explicit owned `{path,sha256}` file, bounded to 1 MB, whose actual bytes must match. These can be independently authenticated by the external gate owner; hashing alone does not authenticate their author or truth.
- `license`: exactly `{reviewed:true,input_use:true,output_redistribution:true,reference:{path,sha256}}`. These are review declarations. Source validates their shape and referenced bytes; D5 verifies approval authority and cohort rights.
- `audio`: exactly `{path,sha256,pcm_sha256,format:"PCM16LE",channels:1,sample_rate:24000,frame_count,duration_seconds}`. Both full-WAV and actual PCM data hashes must match. The declared duration must match the exact frame count within one sample. Nonempty clips up to 120 seconds are accepted; there is no one-second minimum.
- `utterances`: 1–8 ordered objects, each exactly `{utterance_id,start_frame,end_frame,text:{path,sha256}}`. Windows are nonempty, nonoverlapping half-open frame intervals in the actual WAV; leading/inter-utterance/trailing silence is permitted. The text asset is bounded UTF-8, stripped at its outer whitespace, with 1–2000 characters and no internal controls/newlines. The hash always describes the original bytes. Raw wording is loaded privately only for actual task binding, never placed in validation/import receipts.
- `task_bindings`: 1–144 objects, each exactly `{task_id,logical_turn_ids,utterance_ids}`. The utterance IDs must match the fixture's entire ordered utterance list, and the exact logical-turn IDs must match the frozen task at binding time. IDs cannot be bound to multiple fixtures. One fixture row can intentionally serve V/C/K and repeated tasks through separate explicit bindings in the same family/speaker/template cell.

Every distinct fixture row must have a distinct full-WAV hash **and** PCM hash. This rejects both one-file-many-cell relabeling and identical PCM wrapped in different WAV headers. Shared text/provenance assets are allowed only when a repeated path always has the same hash. Reuse across arms/repeats belongs in one row's explicit task bindings.

## File and import boundaries

Only relative owned paths are accepted. Components use ASCII letters, digits, underscores, hyphens and dots; traversal, colons, links/junctions, sensitive credential paths and Windows device names are rejected. No directory discovery, credential reads, downloads or provider calls occur. The canonical audio subset is strict classic RIFF/WAVE with one 16-byte PCM `fmt ` chunk followed by one even-sized `data` chunk. Unknown/duplicate chunks, non-PCM/extensible formats, incorrect byte rate/alignment/rate/channels, truncated chunks, wrong RIFF lengths and trailing unexplained bytes are refused rather than silently normalized.

`validate_catalog(root:Path,catalog:dict,*,reader)` reads only the explicit reviewed assets and returns a metadata-only structural receipt: catalog/run/plan IDs and hash, fixture/task/utterance counts, asset path/hash/kind references and fixed fixture metadata. It returns no spoken wording and issues no custody or rights approval.

`bind_catalog_task(spec,task)` expects `spec.fixture_root`, relative `spec.fixture_catalog`, `spec.run_id`, `spec.trial_plan_hash`, and optionally `spec.fixture_catalog_hash` (canonical catalog hash). It checks exact fixture/family/speaker/task/turn bindings, then returns the original spec plus:

```
_fixture: {
  ... reviewed fixture declarations,
  split: "dev", kind: "reviewed_catalog_utterances",
  audio: {path,sha256,pcm_sha256,format,channels,sample_rate,frame_count,duration_seconds},
  turns: [private reviewed utterance strings],
  utterances: [{utterance_id,start_frame,end_frame,text_sha256,text_reference,audio_sha256,sample_rate}],
  task_binding: {task_id,logical_turn_ids,utterance_ids}
},
_input_wav: absolute actual owned WAV path,
_catalog_hash: canonical catalog SHA256,
_fixture_manifest_hash: canonical selected fixture-row SHA256
```

Keep `turns`, private paths and text references in memory; emit only approved IDs/hashes through the harness privacy projection. The canonical parent adapter is `activation.bind_task_fixture`; it selects this helper when `fixture_catalog` is present. A selected frozen cohort must also compare these actual hashes with D5's frozen fixture manifest before exposure.

`import_catalog(source_root:Path,catalog_path:Path,destination:Path,*,public_access,references)` is future explicit import source. `catalog_path` is relative to the source root. It validates first, requires a new destination with an existing owned parent outside the source tree, and copies only listed/hash-verified audio, text and reference assets through exclusive file creation. Each complete write uses the exact guarded, hash-verified source bytes; it does not claim a separate readback. It then writes `catalog.json` and `import-receipt.json`. Existing files are never overwritten or deleted. An interrupted/failed import can leave a partial directory and is not complete until the final catalog and structural receipt exist. The module never generates missing assets or repairs unknown formats.

Canonical catalog/fixture hashes use sorted compact ASCII-escaped JSON plus one newline. They pin declarations and references, not independent authority. Actual WAV hashes describe original bytes; PCM hashes describe the exact `data` payload. No asset import has been executed during Dirty Mode, and no example data is presented as a licensed corpus.

R4 guarded access: public validation/binding/import require D5 guarded readers or an actual PublicReadAccess with its exact signed artifact inventory; absence is refused before byte reads. The catalog and every named text/WAV/provenance/license asset must appear uniquely in that authenticated inventory under the same owned root. Import also checks that the existing destination parent is an authorized derivative output root before reading/copying. New output remains structurally described, never independently approved. The low-level private `_read`/`_wav` helpers remain available to D4 after its own real licensed-control approval; they do not grant access through these public catalog APIs.
