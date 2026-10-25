# Public development instrumentation checks

`python evals/public_dev.py --out <new-owned-directory>` runs only inline,
team-authored scorer inputs and a fictional task/accounting SQLite journey.
It retains failed checks before returning nonzero. Existing output refuses;
there is no deletion, credential, provider, hidden-input or rights-verifier mode.

The canonical report builder runs twice and must produce identical bytes. An
18-success/one-missing-terminal case must keep 19 starts and an unattained p95;
one unresolved zero-price mock attempt must remain incomplete. Qualification
booleans are fabricated test inputs, never spoken-answer evidence. Separate
interpreters create and reopen the SQLite journals, replay the confirmation,
and verify a single booking and immutable unknown/duplicate accounting.

`Dockerfile.eval` uses a digest-pinned Python image and exact dependency pins
matching D8's frozen environment (lock SHA256
`2c6b38bf3772acf0f7cdc76c2d18899ac81e91c523b31e4b323b386940717e9a`).
The Docker context allowlist excludes credentials, environment files, local
evidence and virtual environments. No project installation or .env loading is
needed; source imports come from `/app/src`.

After the project-specific agent-ports claim/check, build with
`docker build -f Dockerfile.eval -t m6-r03-rv2-eval:<candidate> <worktree>`.
Run an assigned `m6-` container with `--network none`, `--cap-drop ALL`,
`--security-opt no-new-privileges` and the claim's `agentports.*` labels.
The image runs as UID 10001. No port publish, volume or network resource is
needed. Preserve the stopped container and copy `/evidence/m6-public-dev`
to a fresh owned evidence directory; do not use automatic removal.

This is offline scorer/storage instrumentation, not a speech corpus, browser
journey, physical audio measurement, real provider comparison, W7 custody,
saved-artifact CLI report success or W8 product acceptance. The production
artifact-read gates stay in force. RV1 independently reviews these artifacts.
