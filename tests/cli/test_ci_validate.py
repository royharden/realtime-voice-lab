"""Reporter integrity controls; every child pytest process is replaced by a stub."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'ci_validate.py'
POSITIVE = ('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
            '<testcase classname="control" name="executed"/></testsuite></testsuites>')
FIXTURES = Path(__file__).with_name('fixtures')


def case_report(content):
    return POSITIVE.replace('name="executed"/>', f'name="executed">{content}</testcase>')


@pytest.fixture
def run_validation(monkeypatch, tmp_path, capsys):
    spec = importlib.util.spec_from_file_location('ci_validate_under_test', SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    tests_root = tmp_path / 'test-checkout'
    source_root = tmp_path / 'source-checkout'
    tests_root.mkdir()
    source_root.mkdir()
    monkeypatch.setattr(runner, 'ROOT', tests_root)
    calls = []

    def check_output(command, **kwargs):
        assert kwargs == {'text': True}
        assert command[:2] == ['git', '-C']
        if command[3:] == ['rev-parse', 'HEAD']:
            return ('a' if command[2] == str(tests_root) else 'b') * 40 + '\n'
        if command[3:] == ['diff', '--stat']:
            return ' scripts/ci_validate.py | changed\n'
        assert command[3:] == ['status', '--short']
        return ' M attribution-control\n'

    monkeypatch.setattr(runner.subprocess, 'check_output', check_output)
    for key in ('VOICELAB_TEST_PORT', 'VOICELAB_TEST_PORT_CLAIM', 'VOICELAB_PORT_CHECKED'):
        monkeypatch.delenv(key, raising=False)

    def run(xml=POSITIVE, returncode=0, *, timeout=False, phase='unit', preflight=False):
        out = tmp_path / f'evidence-{len(list(tmp_path.glob("evidence-*")))}'
        if preflight:
            for key in ('VOICELAB_TEST_PORT', 'VOICELAB_TEST_PORT_CLAIM', 'VOICELAB_PORT_CHECKED'):
                monkeypatch.setenv(key, 'stub-only-no-listener')

        def child(command, **kwargs):
            calls.append((command, kwargs))
            assert command[1:3] == ['-m', 'pytest']
            assert kwargs['capture_output'] is True
            assert kwargs['timeout'] == 17
            assert kwargs['env']['PYTHONPATH'] == str(source_root / 'src')
            if timeout:
                raise subprocess.TimeoutExpired(command, 17, output=b'partial stdout\n',
                                                stderr=b'partial stderr\n')
            report = Path(next(arg.split('=', 1)[1] for arg in command
                               if arg.startswith('--junitxml=')))
            if xml is not None:
                report.write_text(xml, encoding='utf-8')
            return subprocess.CompletedProcess(command, returncode, b'child stdout\n',
                                               b'child stderr\n')

        monkeypatch.setattr(runner.subprocess, 'run', child)
        monkeypatch.setattr(runner.sys, 'argv', [str(SCRIPT), '--out', str(out),
                            '--source', str(source_root), '--phase', phase, '--timeout', '17'])
        exitcode = runner.main()
        receipt = json.loads((out / 'validation.json').read_text(encoding='utf-8'))
        assert json.loads(capsys.readouterr().out) == receipt
        assert receipt['candidate'] == 'a' * 40
        assert receipt['source_commit'] == 'b' * 40
        assert receipt['source_checkout'] == str(source_root)
        assert receipt['working_tree'] == receipt['source_status'] == ' M attribution-control\n'
        assert receipt['diff_stat'] == ' scripts/ci_validate.py | changed\n'
        for item in receipt['phases']:
            command = item['command']
            assert command[command.index('-c') + 1] == str(source_root / 'pyproject.toml')
            assert command[command.index('-o') + 1] == f'pythonpath={source_root / "src"}'
            if item['phase'] == 'unit':
                assert str(tests_root / 'tests') in command
                assert '--ignore=' + str(tests_root / 'tests/cli') in command
            elif item['phase'] == 'cli':
                assert str(tests_root / 'tests/cli') in command
            else:
                assert str(tests_root / 'tests/browser/test_ui_journey.py') in command
            if item['status'] != 'not_run':
                expected = (b'partial stdout\npartial stderr\n' if timeout
                            else b'child stdout\nchild stderr\n')
                assert (out / f'{item["phase"]}.log').read_bytes() == expected
                if not timeout:
                    assert item['report'] == str(out / f'{item["phase"]}.xml')
                    if xml is not None:
                        assert Path(item['report']).read_text(encoding='utf-8') == xml
        return exitcode, receipt['phases'], calls

    return run


@pytest.mark.parametrize('xml,expected_tests', [
    (POSITIVE, 1),
    (POSITIVE.removeprefix('<testsuites>').removesuffix('</testsuites>'), 1),
    (POSITIVE.replace('<testsuites>', '<testsuites tests="2" failures="0" errors="0" skipped="0">')
             .replace('</testsuites>', POSITIVE.split('<testsuites>')[1]), 2),
])
def test_positive_actual_testcases_pass(run_validation, xml, expected_tests):
    """what_bug_this_catches: rejecting real matching pytest reports or miscounting suites."""
    exitcode, phases, calls = run_validation(xml)
    assert exitcode == 0
    assert phases[0]['status'] == 'passed'
    assert phases[0]['counts'] == {'tests': expected_tests, 'failures': 0, 'errors': 0, 'skipped': 0}
    assert phases[0]['returncode'] == 0
    assert len(calls) == 1


@pytest.mark.parametrize('family', ['xunit1', 'xunit2'])
@pytest.mark.parametrize('root_kind', ['testsuites', 'testsuite', 'multiple-suites'])
def test_real_pytest_properties_and_captured_output_pass(run_validation, family, root_kind):
    """what_bug_this_catches: structural guards rejecting genuine pytest metadata/output."""
    xml = (FIXTURES / f'pytest-8.3.4-{family}-properties-output.xml').read_text(encoding='utf-8')
    # These are captured pytest 8.3.4 reports; only host/path/timing were normalized.
    assert '<properties>' in xml
    assert '<system-out>' in xml and '<system-err>' in xml
    assert '&lt;failure' in xml and '&lt;error' in xml and '&lt;skipped' in xml
    suite = xml[xml.index('<testsuite '):xml.index('</testsuite>') + len('</testsuite>')]
    if root_kind == 'testsuite':
        xml = suite
    elif root_kind == 'multiple-suites':
        xml = f'<testsuites tests="2" failures="0" errors="0" skipped="0">{suite}{suite}</testsuites>'
    exitcode, phases, _ = run_validation(xml)
    assert exitcode == 0
    assert phases[0]['status'] == 'passed'
    assert phases[0]['counts'] == {'tests': 2 if root_kind == 'multiple-suites' else 1,
                                   'failures': 0, 'errors': 0, 'skipped': 0}


@pytest.mark.parametrize('tag', ['failure', 'error', 'skipped'])
@pytest.mark.parametrize('location', ['testcase-wrapper', 'suite', 'root'])
def test_nested_and_misplaced_outcomes_reject_rc0(run_validation, tag, location):
    """what_bug_this_catches: RV1-RUNNER-001-R2 nested and suite-level false greens."""
    if location == 'testcase-wrapper':
        # Exact reviewer shape, with a tag for each nonpassing outcome.
        xml = ('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
               f'<testcase><wrapper><{tag}/></wrapper></testcase></testsuite></testsuites>')
    elif location == 'suite':
        xml = POSITIVE.replace('</testsuite>', f'<{tag}/></testsuite>')
    else:
        xml = POSITIVE.replace('</testsuites>', f'<{tag}/></testsuites>')
    exitcode, phases, _ = run_validation(xml)
    assert exitcode == 1
    assert phases[0]['status'] == 'incomplete'
    assert phases[0]['returncode'] == 0
    assert 'unsupported JUnit structure' in phases[0]['reason']


@pytest.mark.parametrize('tag', ['failure', 'error', 'skipped'])
@pytest.mark.parametrize('namespace', ['prefixed', 'default'])
def test_namespaced_outcomes_reject_rc0(run_validation, tag, namespace):
    """what_bug_this_catches: namespaced outcome elements silently escaping direct counts."""
    outcome = (f'<other:{tag} xmlns:other="urn:unsupported"/>' if namespace == 'prefixed'
               else f'<{tag} xmlns="urn:unsupported"/>')
    exitcode, phases, _ = run_validation(case_report(outcome))
    assert exitcode == 1
    assert phases[0]['status'] == 'incomplete'
    assert phases[0]['returncode'] == 0
    assert 'unsupported JUnit structure' in phases[0]['reason']


@pytest.mark.parametrize('tag', ['failure', 'error', 'skipped'])
@pytest.mark.parametrize('container', ['properties', 'property', 'system-out', 'system-err',
                                      'failure', 'error', 'skipped'])
def test_metadata_and_text_leaves_cannot_hide_outcomes(run_validation, tag, container):
    """what_bug_this_catches: recognized metadata/output/outcomes hiding nested XML outcomes."""
    content = f'<{container}><{tag}/></{container}>'
    if container == 'property':
        content = f'<properties>{content}</properties>'
    xml = case_report(content)
    for outcome, key in [('failure', 'failures'), ('error', 'errors'), ('skipped', 'skipped')]:
        if container == outcome:
            xml = xml.replace(f'{key}="0"', f'{key}="1"')
    exitcode, phases, _ = run_validation(xml)
    assert exitcode == 1
    assert phases[0]['status'] == 'incomplete'
    assert 'unsupported JUnit structure' in phases[0]['reason']


@pytest.mark.parametrize('xml', [
    POSITIVE.replace('</testsuites>', '<wrapper><failure/></wrapper></testsuites>'),
    POSITIVE.replace('</testsuite>', '<wrapper><error/></wrapper></testsuite>'),
    POSITIVE.replace('</testsuite>', '<properties><failure/></properties></testsuite>'),
    POSITIVE.replace('</testsuite>', '<properties><property><skipped/></property></properties>'
                     '</testsuite>'),
    case_report('<wrapper/>'),
    case_report('<properties><wrapper/></properties>'),
    case_report('<system-out><wrapper/></system-out>'),
    case_report('<system-err><wrapper/></system-err>'),
    case_report('<properties><property><wrapper/></property></properties>'),
    case_report('<property name="misplaced" value="x"/>'),
    POSITIVE.replace('</testsuites>', '<system-out>misplaced output</system-out></testsuites>'),
    POSITIVE.replace('</testsuite>', '<system-err>misplaced output</system-err></testsuite>'),
    POSITIVE.replace('<testsuites>', '<testsuites xmlns="urn:unsupported">'),
    POSITIVE.replace('<testsuite ', '<testsuite xmlns="urn:unsupported" '),
    POSITIVE.replace('<testcase ', '<testcase xmlns="urn:unsupported" '),
    case_report('<properties xmlns="urn:unsupported"/>'),
    case_report('<properties><property xmlns="urn:unsupported"/></properties>'),
    case_report('<system-out xmlns="urn:unsupported">output</system-out>'),
])
def test_unsupported_wrappers_placements_and_namespaces_cannot_pass(run_validation, xml):
    """what_bug_this_catches: accepting unsupported XML because ordinary case counts match."""
    exitcode, phases, _ = run_validation(xml)
    assert exitcode == 1
    assert phases[0]['status'] == 'incomplete'
    assert phases[0]['returncode'] == 0
    assert phases[0]['reason'].startswith('invalid JUnit report:')


@pytest.mark.parametrize('tag', ['failure', 'error', 'skipped'])
def test_malformed_second_suite_cannot_pass(run_validation, tag):
    """what_bug_this_catches: inspecting only the first suite's outcome structure."""
    malformed = case_report(f'<wrapper><{tag}/></wrapper>')
    xml = POSITIVE.replace('</testsuites>', malformed.removeprefix('<testsuites>'))
    exitcode, phases, _ = run_validation(xml)
    assert exitcode == 1
    assert phases[0]['status'] == 'incomplete'
    assert 'unsupported JUnit structure' in phases[0]['reason']


@pytest.mark.parametrize('xml,reason', [
    (None, 'invalid JUnit report'),
    ('', 'invalid JUnit report'),
    ('<testsuites>', 'invalid JUnit report'),
    ('<testsuites/>', 'nonempty flat'),
    ('<other/>', 'expected testsuite'),
    ('<testsuite tests="0" failures="0" errors="0" skipped="0"/>', 'no actual testcases'),
    (POSITIVE.replace('tests="1"', 'tests="0"'), 'contradicts'),
    (POSITIVE.replace('tests="1"', 'tests="2"'), 'contradicts'),
    (POSITIVE.replace('<testcase classname="control" name="executed"/>', ''), 'contradicts'),
    (POSITIVE.replace('tests="1"', 'tests="-1"'), 'invalid or missing tests'),
    (POSITIVE.replace('tests="1"', 'tests="1.0"'), 'invalid or missing tests'),
    (POSITIVE.replace('tests="1"', 'tests="x"'), 'invalid or missing tests'),
    (POSITIVE.replace('tests="1"', 'tests="١"'), 'invalid or missing tests'),
    (POSITIVE.replace(' failures="0"', ''), 'invalid or missing failures'),
    (POSITIVE.replace('errors="0"', 'errors="-1"'), 'invalid or missing errors'),
    (POSITIVE.replace('skipped="0"', 'skipped="1"'), 'contradicts'),
    (POSITIVE.replace('<testsuites>', '<testsuites tests="2">'), 'contradicts'),
    (POSITIVE.replace('<testsuites>', '<testsuites tests="bad">'), 'invalid or missing tests'),
    (POSITIVE.replace('name="executed"/>', 'name="executed"><failure/></testcase>'), 'contradicts'),
    (POSITIVE.replace('name="executed"/>', 'name="executed"><error/></testcase>'), 'contradicts'),
    (POSITIVE.replace('name="executed"/>', 'name="executed"><skipped/></testcase>'), 'contradicts'),
    (POSITIVE.replace('name="executed"/>', 'name="executed"><failure/><error/></testcase>'),
     'contradictory outcome'),
    (POSITIVE.replace('<testcase', '<wrapper><testcase')
             .replace('/></testsuite>', '/></wrapper></testsuite>'), 'direct testsuite'),
    (POSITIVE.replace('</testsuites>', '<testcase name="outside"/></testsuites>'), 'outside'),
    ('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
     + POSITIVE + '</testsuite></testsuites>', 'nonempty flat'),
])
def test_missing_empty_malformed_and_contradictory_reports_do_not_pass(run_validation, xml, reason):
    """what_bug_this_catches: rc0 laundering missing, empty or invented execution evidence."""
    exitcode, phases, _ = run_validation(xml)
    assert exitcode == 1
    assert phases[0]['status'] == 'incomplete'
    assert phases[0]['returncode'] == 0
    assert reason in phases[0]['reason']


@pytest.mark.parametrize('tag,key,status', [('failure', 'failures', 'failed'),
                                          ('error', 'errors', 'failed'),
                                          ('skipped', 'skipped', 'incomplete')])
@pytest.mark.parametrize('returncode', [0, 5])
def test_actual_nonpassing_outcomes_cannot_pass(run_validation, tag, key, status, returncode):
    """what_bug_this_catches: rc0 overriding a failed/error/skipped report or rc5 losing failure."""
    xml = POSITIVE.replace(f'{key}="0"', f'{key}="1"').replace(
        'name="executed"/>', f'name="executed"><{tag}/></testcase>')
    exitcode, phases, _ = run_validation(xml, returncode)
    assert exitcode == 1
    assert phases[0]['status'] == ('failed' if returncode else status)
    assert phases[0]['counts'] == {'tests': 1, 'failures': int(tag == 'failure'),
                                   'errors': int(tag == 'error'), 'skipped': int(tag == 'skipped')}
    assert phases[0]['reason']


@pytest.mark.parametrize('xml', [POSITIVE, None, '<malformed',
                               case_report('<wrapper><failure/></wrapper>'),
                               POSITIVE.replace('</testsuite>', '<failure/></testsuite>'),
                               case_report('<failure xmlns="urn:unsupported"/>')])
def test_subprocess_failure_remains_failed_with_any_report(run_validation, xml):
    """what_bug_this_catches: a valid report or invalid evidence erasing subprocess failure."""
    exitcode, phases, _ = run_validation(xml, 5)
    assert exitcode == 1
    assert phases[0]['status'] == 'failed'
    assert phases[0]['returncode'] == 5


def test_timeout_preserves_partial_log_and_explicit_status(run_validation):
    """what_bug_this_catches: timeout losing partial evidence or becoming a pass."""
    exitcode, phases, _ = run_validation(timeout=True)
    assert exitcode == 1
    assert phases[0]['status'] == 'timeout'
    assert phases[0]['returncode'] is None


def test_listener_preflight_still_blocks_without_spawning(run_validation):
    """what_bug_this_catches: reporter checks bypassing the operator port preflight."""
    exitcode, phases, calls = run_validation(phase='cli')
    assert exitcode == 1
    assert phases[0]['status'] == 'not_run'
    assert not calls


@pytest.mark.parametrize('xml', ['<malformed', case_report('<wrapper><failure/></wrapper>'),
                               case_report('<skipped xmlns="urn:unsupported"/>')])
def test_invalid_report_records_each_phase_and_returns_nonzero(run_validation, xml):
    """what_bug_this_catches: invalid XML aborting before later phase receipts are retained."""
    exitcode, phases, calls = run_validation(xml, phase='all', preflight=True)
    assert exitcode == 1
    assert [item['phase'] for item in phases] == ['unit', 'cli', 'browser']
    assert all(item['status'] == 'incomplete' for item in phases)
    assert len(calls) == 3
