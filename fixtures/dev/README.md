# Development fixtures

Source instructions only; no speech or measured evidence. After separate runtime activation, `python -m voicelab.harness.cli generate-dev-fixture --out <new-owned-directory>` generates silence and a 440 Hz diagnostic WAV, hashes and provenance. No voice model, account or licensed speaker is used. No audio was generated during construction.

The generated manifest starts with `license.reviewed=false`. The operator must review generator/provenance and supply a review reference before driver activation. This does not establish speech redistribution rights. Silence is the fake microphone input so background signals do not create extra voice turns. Labeled typed words drive interpretation; real capture, relay, authority, wire parsers, playback and accounting stay in the path.

Default words: `hours`, `list slots`, `select slot 1`, `confirm`, `status`. Each fresh session resets its own fictional namespace without deleting journals. V/C/K use the same words and signal. This cannot satisfy the planned speech corpus, heldout custody, spoken-task oracle or live acceptance.

Use a five-turn D5 plan, or supply a separately reviewed development manifest with exact prespecified words for each scheduled turn. Missing wording is rejected. D5 owns balanced schedules and fixture/custody gates; the harness has no heldout release switch.
