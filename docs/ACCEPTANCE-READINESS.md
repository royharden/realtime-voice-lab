# Public acceptance-readiness inventory

`scripts/acceptance_readiness.py` inventories explicitly supplied public metadata,
without opening referenced audio, traces, ledgers, oracle files or custody records.
It performs no network calls, device access, artifact writes, seal creation,
release redemption or acceptance decision. Run it in the installed local project
environment (Python 3.12 and the locked dependencies).

```powershell
.venv\Scripts\python.exe scripts/acceptance_readiness.py --format text
.venv\Scripts\python.exe scripts/acceptance_readiness.py --public-root C:/public-m6-metadata --manifest run.json --manifest-sha256 <exact-sha256> --evidence evidence.json --evidence-sha256 <exact-sha256> --source-commit <source-git-commit> --runtime-commit <tested-runtime-git-commit> --format json
```

The default inventory reports absent evidence. Exit **1** means blocked/unresolved;
exit **2** means invalid input or CLI usage. This command never emits a product pass
or exit 0. JSON goes to stdout by default; `--format text` gives a human-readable
inventory. Repeated invocation with unchanged inputs/checker produces the same
bytes; it adds no timestamps. Shell redirection is the operator's choice.

Supply only separately exported **public** metadata under `--public-root`. No
automatic discovery, recursive searches or referenced-file reads occur. The root
and metadata paths reject links and protected/private or credential-like locations;
paths must be relative forward-slash JSON paths. These guards are defense in depth,
not an authentication mechanism: do not point the tool at secrets or private custody
material. Hash pins must come from the actual public-artifact owner; the command
does not obtain rights merely because the pins match.

The same conservative component policy applies to metadata names and every root
ancestor, before any filesystem inspection or reader construction. Dot-prefixed
names, `gold`, `acceptance`, `auth`, `private`, and names containing `credential`,
`password`, `secret`, `token`, `ssh`, `holdout` or `heldout` (case-insensitive) are
refused. Incidental fragments in ordinary ancestor names are also refused: export
public metadata to a separately named public directory in that case. Root links and
reparse points are refused. Metadata paths also refuse Windows drive/root spellings,
dot/empty components, trailing spaces/dots and ambiguous Windows component characters.
Windows reserved device components and network/device or drive-relative root aliases
are refused before filesystem inspection as well.

The manifest uses the existing closed `voicelab.contracts.RunManifest` schema.
The optional evidence file is a JSON array of existing closed `EvidenceRecord`
objects. Existing `ArtifactReader` checks exact metadata bytes, size and path
identity; duplicate keys and nonfinite JSON are refused. Records must join the
manifest run and, when supplied, its scheduled task identities. Duplicate trials,
inconsistent unknown reasons and development custody claims are rejected. Missing
records, denominators, audio/oracle references and observations remain visible.
Artifact hashes inside those records are listed only: their bytes are **not** read
or validated, and the inventory never treats a reference as a successful execution.

Nonfinite checks cover all nested numeric values, including ordinary JSON exponent
syntax that overflows a float (such as `1e999`); finite exponents remain valid.
Manifest scheduled task identities must be unique, and an unknown observation must
give a reason containing non-whitespace text. These checks preserve the supplied
denominator and make absent observations explicit; they do not resolve acceptance.

Source and tested runtime Git commits are separate operator-declared 40-hex pins.
They may differ (for example, reviewed documentation after an unchanged runtime).
Neither is equated to `RunManifest.code_hash`, a 64-hex source-content hash. The
manifest's code/build/config/schema/fixture/plan/calibration/price pins are retained
exactly. The script does not verify Git objects, map a build to those commits or
prove execution; such bindings require independent execution evidence. The checker
reports its own exact source SHA-256 separately. Public mock/replay development
metadata is labeled synthetic rehearsal, never live evidence or W7/W8.

The unresolved prerequisites come from selected plan sections 4, 6 and 8 and
packages W7/W8. They request genuine rights/public-read authority, approved live
budget/prices/capabilities and paired tasks, measured browser calibration, full
offline/live soak and cleanup, independent custody/disjointness/freeze/release,
spoken-audio and confirmation adjudication, and an independent nonauthor review.
Supplying strings, self-issued seals or booleans cannot satisfy them here. Even a
complete public metadata inventory remains **blocked** because this read-only
command cannot authenticate those prerequisites or make the later INT decision.

The actual custody/release and saved-evidence workflows remain in
`voicelab.evaluation.custody`, `gates`, `exposure`, `assembly` and `trust`.
This inventory does not call their authorization or exposure operations and does
not replace them. See [limitations](LIMITATIONS.md) and
[validation](VALIDATION.md) for the accepted offline wave's evidence and limits.
