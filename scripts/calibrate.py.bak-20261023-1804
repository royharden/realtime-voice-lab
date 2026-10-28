"""Analyze saved calibration repeats; does not calibrate a device or launch an app."""
import argparse
import json
from pathlib import Path

from voicelab.reporting.diagnostics import calibration_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observations', required=True, help='JSON list of actual observed repeats')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    observed = json.loads(Path(args.observations).read_text(encoding='utf-8'))
    rows = observed.get('rows', []) if isinstance(observed, dict) else observed
    report = calibration_report(rows)
    controls = observed.get('controls', {}) if isinstance(observed, dict) else {}
    report['controls'] = controls
    required = ('app_worklet_anchor_valid', 'injected_offset_rejected', 'past_scheduling_anchor_valid',
                'fence_ack_observed', 'app_fence_cutoff_within_quantum', 'stale_epoch_frame_refused')
    report['full_gate_pass'] = (report['internal_gate_pass'] and all(controls.get(k) is True for k in required)
                                and observed.get('full_gate_complete') is True) if isinstance(observed, dict) else False
    Path(args.out).write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
