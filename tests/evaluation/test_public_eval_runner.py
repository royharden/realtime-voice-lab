"""Run the owned public evaluator as a real CLI in a fresh interpreter."""
import json
from pathlib import Path
import subprocess
import sys


def test_public_cli_records_oracles_and_persistent_sqlite_without_overwrite(tmp_path):
    """what_bug_this_catches: a runner exit-zero without durable checks, or reruns rewriting evidence."""
    root = Path(__file__).resolve().parents[2]
    out = tmp_path / 'm6-public-eval'
    command = [sys.executable, str(root / 'evals/public_dev.py'), '--out', str(out)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr + result.stdout
    summary = json.loads((out / 'eval-summary.json').read_text())
    assert summary['passed'] is True and len(summary['checks']) == 2
    assert all(check['status'] == 'passed' for check in summary['checks'])
    assert (out / 'm6-task.sqlite').is_file() and (out / 'm6-ledger.sqlite').is_file()
    before = (out / 'eval-summary.json').read_bytes()
    refused = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert refused.returncode != 0
    assert (out / 'eval-summary.json').read_bytes() == before
