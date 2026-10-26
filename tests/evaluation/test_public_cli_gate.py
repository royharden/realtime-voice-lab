"""The public instrumentation harness must not become an artifact-read bypass."""
import pytest

from voicelab.evaluation.cli import main


def test_saved_artifact_report_stays_refused_without_actual_read_verifier(tmp_path, capsys):
    """what_bug_this_catches: offline mode or public labels silently unlocking file reads."""
    out = tmp_path / 'not-created'
    with pytest.raises(SystemExit) as error:
        main(['report', '--manifest', str(tmp_path / 'public-manifest.json'),
              '--rows', str(tmp_path / 'public-rows.jsonl'), '--ledger', str(tmp_path / 'public-ledger.json'),
              '--out', str(out)])
    assert error.value.code == 2
    assert 'no actual read verifier' in capsys.readouterr().err
    assert not out.exists()
