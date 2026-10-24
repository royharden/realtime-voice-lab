"""Append raw driver observations; canonical metrics remain owned by evaluation."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .privacy import observed_metadata, decoded_metadata


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


class EvidenceSink:
    def __init__(self, root: Path, spec: dict):
        # Never mix a previous run's observations or silently overwrite artifacts.
        if root.exists():
            # An operator may precreate an empty directory to bind its real inode
            # in D5's signed allowed_output_root_bindings before this invocation.
            from voicelab.evaluation.exposure import require_output_root
            require_output_root(spec.get('_output_reader'), root)
            if not root.is_dir() or root.is_symlink() or any(root.iterdir()):
                raise ValueError('only_authenticated_precreated_empty_output_root_allowed')
        else:
            root.mkdir(parents=True, exist_ok=False)
        self.root, self.run_id = root, spec['run_id']
        self.mode = spec.get('mode') if spec.get('mode') in {'mock', 'live', 'replay'} else 'unknown'
        self.handle = (root / 'driver.jsonl').open('xb')
        self.sequence = 0
        self.spec_hash = spec['_manifest_hash']
        self.counts: dict[str, int] = {}
        self.closed = False
        self.capacity_exhausted = False
        self.authoritative_references: dict[tuple[str, str], dict] = {}

    def emit(self, kind: str, *, observation: str = 'observed', **fields: object) -> None:
        critical = kind in {'driver_ended', 'task_admission', 'turn_not_started', 'cohort_obligation',
                           'cohort_cleanup_observed', 'task_driver_ended', 'cleanup_unknown'}
        if self.closed or self.sequence >= (100_000 if critical else 95_000):
            self.capacity_exhausted = True
            raise ValueError('driver evidence closed or bounded event capacity exhausted')
        if observation not in {'observed', 'estimated', 'unknown'}:
            raise ValueError('invalid observation status')
        if 'mode' in fields and fields['mode'] != self.mode:
            raise ValueError('evidence mode cannot change inside a frozen run')
        row = {'schema_version': 1, 'kind': kind, 'run_id': self.run_id,
               'sequence': self.sequence, 'timestamp': time.monotonic_ns(),
               'clock_domain': 'driver_monotonic_ns', 'observation': observation,
               'mode': self.mode, 'split': 'dev', **fields}
        self.handle.write(canonical(observed_metadata(row)))
        self.handle.flush()
        self.sequence += 1
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def artifact(self, name: str, value: object, kind: str) -> dict:
        return self.bytes_artifact(name, canonical(observed_metadata(value)), kind)

    def bytes_artifact(self, name: str, raw: bytes, kind: str) -> dict:
        if Path(name).name != name:
            raise ValueError('flat owned output name required')
        if kind == 'trace':
            # Preserve exact authoritative hashes only when every raw trace row is
            # already metadata-only. Reject before persistence; never scrub then
            # mislabel the changed bytes with the producer's original digest.
            for line in raw.decode('utf-8').splitlines():
                if line.strip():
                    value = decoded_metadata(json.loads(line))
                    if not isinstance(value, dict) or observed_metadata(value) != value:
                        raise ValueError('authoritative trace contains unapproved metadata/content fields')
        if kind in {'ledger_snapshot', 'fault_controls'}:
            value = decoded_metadata(json.loads(raw))
            if not isinstance(value, dict) or observed_metadata(value) != value:
                raise ValueError('authoritative snapshot contains unapproved metadata/content fields')
        with (self.root / name).open('xb') as handle:
            handle.write(raw)
        return {'path': name, 'kind': kind, 'sha256': hashlib.sha256(raw).hexdigest()}

    def authoritative_artifact(self, raw: bytes, kind: str) -> dict:
        digest = hashlib.sha256(raw).hexdigest()
        key = (kind, digest)
        if key not in self.authoritative_references:
            self.authoritative_references[key] = self.bytes_artifact(kind + '-' + digest + '.json', raw, kind)
        return self.authoritative_references[key]

    def close(self, *, completed: bool, reason: str, references: dict | None = None) -> None:
        if self.closed:
            return
        self.emit('driver_ended', completed=completed, reason=reason)
        self.handle.close()
        self.closed = True
        raw = (self.root / 'driver.jsonl').read_bytes()
        self.artifact('handoff.json', {
            'schema_version': 1, 'run_id': self.run_id, 'manifest_hash': self.spec_hash,
            'mode': self.mode,
            'evidence_label': f'development {self.mode} prospective/observed records; no independent acceptance',
            'driver_completed': completed, 'counts': self.counts,
            'references': {'driver': {'path': 'driver.jsonl', 'kind': 'driver_observations',
                           'sha256': hashlib.sha256(raw).hexdigest()}, **(references or {})},
            'spoken_correctness': 'unknown', 'acoustic_latency': 'unknown',
            'heldout_release': False, 'product_acceptance': False,
            'missing_evidence_is_failure_or_unknown': True,
        }, 'driver_handoff')
