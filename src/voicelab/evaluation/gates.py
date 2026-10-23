"""Structural gates cannot authenticate a custodian or manufacture independence."""
from __future__ import annotations

from collections.abc import Callable

from .artifacts import ArtifactReader, digest


ReleaseVerifier = Callable[[dict], bool]


def validate_gates(manifest: dict, plan: dict, fixtures: dict, freeze: dict | None,
                   reader: ArtifactReader, *, expected_freeze_hash: str | None = None,
                   release: dict | None = None, verify_release: ReleaseVerifier | None = None) -> dict:
    problems = []
    if manifest["trial_plan_hash"] != plan["plan_hash"] or manifest["run_id"] != plan["run_id"]:
        raise ValueError("run manifest does not pin this plan")
    fixture_rows = fixtures.get("fixtures", [])
    by_id = {row["fixture_id"]: row for row in fixture_rows}
    if len(by_id) != len(fixture_rows):
        raise ValueError("duplicate fixture identities")
    selected_ids = {task["fixture_id"] for task in plan["tasks"]}
    selected = []
    for fixture_id in sorted(selected_ids):
        row = by_id[fixture_id]
        if row["split"] != manifest["split"]:
            raise ValueError("fixture split differs from run split")
        license_info = row.get("license", {})
        if (license_info.get("reviewed") is not True or not license_info.get("reference")
                or license_info.get("output_redistribution") is not True):
            raise ValueError("reviewed fixture/output rights evidence required")
        if row.get("synthetic") is not True or not all(row.get(key) for key in
                ("generator", "generator_version", "voice", "settings", "sample_rate", "wording_family", "speaker", "interruption_schedule")):
            raise ValueError("synthetic fixture generation/license provenance incomplete")
        selected.append(row)
    for task in plan["tasks"]:
        fixture = by_id[task["fixture_id"]]
        if (fixture["wording_family"], fixture["speaker"]) != (task["family"], task["speaker"]):
            raise ValueError("fixture family/speaker differs from prespecified cell")
    if manifest["split"] == "dev":
        development = [row for row in fixture_rows if row.get("split") == "dev"]
        if len({r["wording_family"] for r in development}) < 12 or len({r["speaker"] for r in development}) < 4:
            problems.append("development corpus below 12-family/four-speaker coverage; dev progress only")
    freeze_valid = False
    if freeze is not None and expected_freeze_hash and digest(freeze) == expected_freeze_hash:
        pins = {key: manifest[key] for key in ("code_hash", "build_hash", "config_hash", "schema_hash",
                "fixture_manifest_hash", "trial_plan_hash", "fixture_seal")}
        pins["thresholds_hash"] = digest(manifest["thresholds"])
        pins["endpoint_hash"] = digest(manifest["endpoint"])
        pins["onset_hash"] = digest(manifest["onset"])
        freeze_valid = freeze.get("pins") == pins and freeze.get("frozen_before_exposure") is True
    if not freeze_valid:
        problems.append("externally pinned pre-exposure build/detector/scorer/plan/fixture freeze absent or mismatched")
    independent_release = False
    if manifest["split"] == "heldout":
        if not release or verify_release is None or not verify_release(release):
            raise ValueError("heldout is closed: externally verified independent custody release required")
        if (not freeze_valid or release.get("freeze_hash") != expected_freeze_hash
                or release.get("fixture_manifest_hash") != manifest["fixture_manifest_hash"]
                or release.get("seal") != manifest["fixture_seal"]
                or release.get("candidate_hash") != manifest["code_hash"]
                or release.get("single_use_authorized") is not True
                or not release.get("reference")):
            raise ValueError("heldout release does not bind this frozen, once-authorized candidate")
        for dimension in ("wording_family", "speaker", "interruption_schedule"):
            development = {row[dimension] for row in fixture_rows if row.get("split") == "dev"}
            heldout = {row[dimension] for row in selected}
            if not development or development & heldout:
                raise ValueError("heldout dimension overlap or missing dev comparison: " + dimension)
        if len({r["wording_family"] for r in selected}) < 8 or len({r["speaker"] for r in selected}) < 2:
            raise ValueError("heldout family/speaker coverage incomplete")
        independent_release = True
    # Never expose heldout audio before verifying its independent release/freeze.
    for row in selected:
        if reader.audio(row["audio_artifact"]) != row["sha256"]:
            raise ValueError("fixture WAV hash mismatch")
    return {"split": manifest["split"], "freeze_valid": freeze_valid,
            "independent_release_verified": independent_release,
            "custody_reference": release["reference"] if independent_release else None,
            "limitations": problems, "product_acceptance": False,
            "evidence_label": "independently released evidence inputs; not acceptance" if independent_release else "dev progress only"}
