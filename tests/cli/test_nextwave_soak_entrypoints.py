"""Public synthetic entrypoint contracts; every workload collaborator is inert.

These tests exercise routing and reporting only, never a soak or resource probe.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import runpy
import sys
import types

import pytest

from voicelab.harness import cli

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / 'scripts/soak.py'
LABEL = 'public synthetic test double; no runtime evidence'


def arguments(tmp_path, profile=None):
    result = ['local-synthetic-soak', '--port', '22377', '--port-claim',
              str(tmp_path / 'public claim.json'), '--web-dist', str(tmp_path / 'web dist'),
              '--web-manifest', str(tmp_path / 'web manifest.json'), '--out',
              str(tmp_path / 'public output')]
    return result + (['--profile', profile] if profile else [])


def producer(monkeypatch, result=None, error=None):
    calls = []
    def run(args):
        calls.append(args)
        if error is not None:
            raise error
        return copy.deepcopy(result)
    module = types.ModuleType('voicelab.harness.local_synthetic')
    module.run_local_synthetic = run
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return calls


def payload(coverage=True, gate=True, incomplete=None):
    return {'summary': {'coverage_complete': coverage, 'full_gate_pass': gate,
                        'public_marker': 'preserve me'},
            'incomplete': [] if incomplete is None else incomplete,
            'evidence_label': LABEL, 'product_acceptance': True,
            'private_internal_marker': 'must not project'}


def wrapper_run(monkeypatch, argv, status, *, mutation=None, import_only=False):
    calls = []
    stub = types.ModuleType('voicelab.harness.cli')
    def main(values):
        calls.append(values)
        return status
    stub.main = main
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    monkeypatch.setattr(sys, 'argv', [str(WRAPPER), *argv])
    if import_only:
        runpy.run_path(str(WRAPPER), run_name='public_import_check')
        return calls, None
    with pytest.raises(SystemExit) as exit_info:
        if mutation is None:
            runpy.run_path(str(WRAPPER), run_name='__main__')
        else:
            source = mutation(WRAPPER.read_text(encoding='utf-8'))
            exec(compile(source, str(WRAPPER) + ':countercontrol', 'exec'),
                 {'__name__': '__main__'})
    return calls, exit_info.value.code


@pytest.mark.parametrize('status', [0, 1, 7, None])
@pytest.mark.parametrize('local', [False, True])
def test_wrapper_main_routes_and_propagates_exit(monkeypatch, status, local):
    tail = ['--profile', 'full', '--out', 'public path with spaces',
            '--local-synthetic-extra', '--local-synthetic=true']
    argv = ['--local-synthetic', *tail, '--local-synthetic'] if local else tail
    calls, code = wrapper_run(monkeypatch, argv, status)
    assert calls == [['local-synthetic-soak' if local else 'soak', *tail]]
    assert code == status


def test_wrapper_import_is_inert(monkeypatch):
    assert wrapper_run(monkeypatch, ['--local-synthetic'], 9, import_only=True) == ([], None)


def test_cli_import_is_inert(monkeypatch):
    calls = producer(monkeypatch, error=AssertionError('producer during import'))
    monkeypatch.setattr(sys, 'argv', ['harness.cli', *arguments(Path('public-synthetic'))])
    namespace = runpy.run_path(str(Path(cli.__file__)), run_name='public_cli_import_check')
    assert callable(namespace['main'])
    assert calls == []


@pytest.mark.parametrize('coverage, expected', [(True, 0), (False, 1)])
def test_cli_main_propagates_dispatcher_exit(monkeypatch, tmp_path, coverage, expected):
    calls = producer(monkeypatch, payload(coverage=coverage))
    monkeypatch.setattr(sys, 'argv', ['harness.cli', *arguments(tmp_path)])
    monkeypatch.delitem(sys.modules, 'voicelab.harness.cli')
    with pytest.raises(SystemExit) as caught:
        runpy.run_module('voicelab.harness.cli', run_name='__main__')
    assert caught.value.code == expected
    assert len(calls) == 1


@pytest.mark.parametrize('profile', [None, 'short', 'full'])
def test_dispatcher_profiles_paths_and_projection(monkeypatch, tmp_path, capsys, profile):
    result = payload()
    calls = producer(monkeypatch, result)
    assert cli.main(arguments(tmp_path, profile)) == 0
    assert len(calls) == 1
    args = calls[0]
    assert args.profile == (profile or 'short')
    assert args.port == 22377
    for field, relative in [('port_claim', 'public claim.json'), ('web_dist', 'web dist'),
                            ('web_manifest', 'web manifest.json'), ('out', 'public output')]:
        assert getattr(args, field) == tmp_path / relative
    assert json.loads(capsys.readouterr().out) == {
        'summary': result['summary'], 'incomplete': [],
        'evidence_label': LABEL, 'product_acceptance': False}


@pytest.mark.parametrize('profile', ['short', 'full'])
@pytest.mark.parametrize('coverage', [False, None, 0, 1, 'true', [], {}])
def test_non_boolean_or_false_coverage_never_succeeds(monkeypatch, tmp_path, profile, coverage):
    producer(monkeypatch, payload(coverage=coverage))
    assert cli.main(arguments(tmp_path, profile)) == 1


@pytest.mark.parametrize('summary', [{}, {'full_gate_pass': True}])
def test_missing_coverage_never_succeeds(monkeypatch, tmp_path, summary):
    result = payload()
    result['summary'] = summary
    producer(monkeypatch, result)
    assert cli.main(arguments(tmp_path)) == 1


@pytest.mark.parametrize('gate', [False, None, 0, 1, 'true', [], {}])
def test_full_gate_requires_boolean_true(monkeypatch, tmp_path, gate):
    producer(monkeypatch, payload(gate=gate))
    assert cli.main(arguments(tmp_path, 'full')) == 1


def test_full_gate_missing_refuses_but_short_does_not_require_full_gate(monkeypatch, tmp_path):
    result = payload()
    del result['summary']['full_gate_pass']
    calls = producer(monkeypatch, result)
    assert cli.main(arguments(tmp_path, 'full')) == 1
    assert cli.main(arguments(tmp_path, 'short')) == 0
    assert len(calls) == 2


def test_short_can_complete_without_full_gate_pass(monkeypatch, tmp_path):
    producer(monkeypatch, payload(gate=False))
    assert cli.main(arguments(tmp_path, 'short')) == 0


@pytest.mark.parametrize('profile', ['short', 'full'])
def test_incomplete_coverage_refuses_even_with_true_gate(monkeypatch, tmp_path, profile):
    producer(monkeypatch, payload(incomplete=['public_missing_observation']))
    assert cli.main(arguments(tmp_path, profile)) == 1


def test_producer_exception_propagates_without_success_output(monkeypatch, tmp_path, capsys):
    error = RuntimeError('synthetic producer failure')
    calls = producer(monkeypatch, error=error)
    with pytest.raises(RuntimeError) as caught:
        cli.main(arguments(tmp_path))
    assert caught.value is error
    assert len(calls) == 1
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('bad', ['missing-required', 'invalid-profile', 'invalid-port', 'unknown-option'])
def test_parse_refusal_precedes_producer(monkeypatch, tmp_path, capsys, bad):
    calls = producer(monkeypatch, error=AssertionError('producer reached invalid invocation'))
    argv = arguments(tmp_path)
    if bad == 'missing-required':
        argv = ['local-synthetic-soak']
    elif bad == 'invalid-profile':
        argv += ['--profile', 'overnight']
    elif bad == 'invalid-port':
        argv[argv.index('22377')] = 'not-an-integer'
    else:
        argv += ['--unrecognized']
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2
    assert calls == []
    assert capsys.readouterr().out == ''


def test_default_legacy_soak_reaches_activation_not_local_producer(monkeypatch, tmp_path):
    calls = producer(monkeypatch, error=AssertionError('legacy routed local'))
    sentinel = RuntimeError('inert legacy activation boundary')
    legacy_calls = []
    activation = types.ModuleType('voicelab.harness.activation')
    def load(*args, **kwargs):
        legacy_calls.append((args, kwargs))
        raise sentinel
    activation.load_activation = load
    evidence = types.ModuleType('voicelab.harness.evidence')
    evidence.EvidenceSink = lambda *a, **k: pytest.fail('sink must not be reached')
    monkeypatch.setitem(sys.modules, activation.__name__, activation)
    monkeypatch.setitem(sys.modules, evidence.__name__, evidence)
    manifest = tmp_path / 'public manifest.json'
    with pytest.raises(RuntimeError) as caught:
        cli.main(['soak', '--manifest', str(manifest), '--activation-reference',
                  'public-reference', '--out', str(tmp_path / 'out')])
    assert caught.value is sentinel
    assert legacy_calls == [((manifest, 'public-reference'), {'load_default_fixture': False})]
    assert calls == []


@pytest.mark.parametrize('mutation', [
    lambda s: s.replace("'local-synthetic-soak' if local else 'soak'", "'soak'"),
    lambda s: s.replace('raise SystemExit(main(', 'main(').replace(']))', ']); raise SystemExit(0)'),
])
def test_wrapper_oracle_countercontrols_reject_false_success(monkeypatch, mutation):
    # These in-memory defective wrappers must be caught by the same routing/exit oracle.
    with pytest.raises(AssertionError):
        calls, code = wrapper_run(monkeypatch, ['--local-synthetic'], 7, mutation=mutation)
        assert calls == [['local-synthetic-soak']]
        assert code == 7


def test_dispatcher_oracle_countercontrol_rejects_always_success(monkeypatch, tmp_path):
    # No source file mutation: compile an in-memory defective predicate only.
    source = Path(cli.__file__).read_text(encoding='utf-8')
    start = source.index('        return 0 if (')
    end = source.index('\n    if args.operation', start)
    mutant = source[:start] + '        return 0' + source[end:]
    namespace = {'__name__': 'voicelab.harness.cli_countercontrol',
                 '__package__': 'voicelab.harness'}
    exec(compile(mutant, str(cli.__file__) + ':countercontrol', 'exec'), namespace)
    producer(monkeypatch, payload(coverage=False))
    with pytest.raises(AssertionError):
        assert namespace['main'](arguments(tmp_path)) == 1
