"""Shared synthetic, dev-only builders for the regression contracts.

NOT YET RUN: authored under Dirty Mode as source only (see docs/VALIDATION.md).
No fixture here holds real voices, heldout labels, prices, credentials or provider data.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

# Until pyproject declares pytest pythonpath (requested from INT), import the src layout directly.
SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

ZERO_HASH = '0' * 64
RUN_ID = 'regression-run'


def scheduled_turn(turn: str, **overrides) -> dict:
    """One prespecified clean C-arm assignment with independent start evidence."""
    row = {'logical_turn_id': turn, 'trial_id': 'trial-1', 'arm': 'C', 'cohort': 'clean',
           'network_profile': 'loopback', 'model_version': 'synthetic-pipeline-v1',
           'admission': 'started', 'admission_evidence_reference': 'driver-admission-' + turn}
    row.update(overrides)
    return row


def run_manifest(turns: list[str], assignments: list[dict]) -> dict:
    """Minimal RunManifest-valid mock manifest; hashes are explicit placeholders."""
    return {'run_id': RUN_ID, 'mode': 'mock', 'code_hash': ZERO_HASH, 'build_hash': ZERO_HASH,
            'config_hash': ZERO_HASH, 'schema_hash': ZERO_HASH, 'fixture_manifest_hash': ZERO_HASH,
            'split': 'dev', 'fixture_seal': None, 'task_version': 'task-v1',
            'prompt_version': 'prompt-v1', 'tool_version': 'tool-v1', 'adapters': {}, 'models': {},
            'audio': {}, 'endpoint': {}, 'onset': {}, 'resampler': {}, 'browser': 'synthetic',
            'operating_system': 'synthetic', 'device': 'synthetic', 'network': {}, 'fault_seed': 0,
            'calibration_hash': None, 'trial_plan_hash': ZERO_HASH, 'thresholds': {},
            'pricing_manifest_hash': None, 'authorization_reference': None,
            'evidence_label': 'synthetic regression contract input',
            'created_at': '2026-10-23T00:00:00Z', 'scheduled_turns': turns,
            'turn_assignments': assignments}


def clean_success_row(turn: str, render_ms: float, **overrides) -> dict:
    """A TurnResult-valid qualified clean success whose render latency enters the headline."""
    row = {'run_id': RUN_ID, 'trial_id': 'trial-1', 'logical_turn_id': turn, 'arm': 'C',
           'outcome': 'success', 'task_correct': True, 'spoken_correct': True,
           'receipt_latency_ms': render_ms, 'render_latency_ms': render_ms,
           'timing_boundary': 'browser_output_timestamp_estimate', 'calibration_valid': True,
           'admission': 'started', 'cohort': 'clean', 'timing_domain': 'browser_performance_ms'}
    row.update(overrides)
    return row


def empty_ledger() -> dict:
    """A complete zero-attempt ledger snapshot accepted by cost_analysis."""
    return {'known_cost': '0', 'unknown_exposure': '0', 'unknown_count': 0, 'complete': True,
            'attempts': []}


@pytest.fixture
def report_builders():
    """Expose builders to tests without a shared importable test package."""
    class Builders:
        pass
    builders = Builders()
    builders.scheduled_turn = scheduled_turn
    builders.run_manifest = run_manifest
    builders.clean_success_row = clean_success_row
    builders.empty_ledger = empty_ledger
    builders.run_id = RUN_ID
    return builders
