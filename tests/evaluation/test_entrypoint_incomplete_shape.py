"""Proposed future-producer hardening; public inert doubles, no runtime proof.

what_bug_this_catches: malformed falsey incomplete values must not earn exit zero.
Current internal producers return lists; baseline reds do not prove reachability.
"""
from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path

import pytest

from voicelab.harness import cli

MISSING = object()
BAD = [pytest.param(MISSING, id='missing'), pytest.param(None, id='null'),
       pytest.param(False, id='false'), pytest.param(0, id='zero'),
       pytest.param('', id='empty-string'), pytest.param({}, id='empty-object'),
       pytest.param((), id='empty-tuple')]


class EmptyListSubclass(list):
    """Inert empty list with no custom truthiness or serialization behavior."""


def invoke(monkeypatch, capsys, profile, incomplete, *, coverage=True, gate=True, main=None):
    result = {'summary': {'coverage_complete': coverage, 'full_gate_pass': gate},
              'evidence_label': 'public synthetic stub; no runtime evidence'}
    if incomplete is not MISSING:
        result['incomplete'] = copy.deepcopy(incomplete)
    stub = types.ModuleType('voicelab.harness.local_synthetic')
    calls = []

    def run(args):
        calls.append(args.profile)
        return result

    stub.run_local_synthetic = run
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    code = (main or cli.main)(['local-synthetic-soak', '--profile', profile, '--port', '22392',
                              '--port-claim', 'unused-public-claim', '--web-dist', 'unused-dist',
                              '--web-manifest', 'unused-manifest', '--out', 'unused-output'])
    output = json.loads(capsys.readouterr().out)
    assert calls == [profile]
    assert output['product_acceptance'] is False
    assert output['summary'] == result['summary']
    return code


@pytest.mark.parametrize('profile', ['short', 'full'])
@pytest.mark.parametrize('incomplete', BAD)
def test_malformed_falsey_incomplete_refuses(monkeypatch, capsys, profile, incomplete):
    assert invoke(monkeypatch, capsys, profile, incomplete) == 1


@pytest.mark.parametrize('profile', ['short', 'full'])
@pytest.mark.parametrize('incomplete,expected', [([], 0), (['missing-observation'], 1)])
def test_real_list_contract_preserved(monkeypatch, capsys, profile, incomplete, expected):
    assert invoke(monkeypatch, capsys, profile, incomplete) == expected


@pytest.mark.parametrize('profile', ['short', 'full'])
def test_empty_list_subclass_refuses(monkeypatch, capsys, profile):
    # what_bug_this_catches: isinstance(list) incorrectly accepts a list subclass.
    assert invoke(monkeypatch, capsys, profile, EmptyListSubclass()) == 1


@pytest.mark.parametrize('profile', ['short', 'full'])
def test_oracle_distinguishes_isinstance_from_exact_list(monkeypatch, capsys, profile):
    # what_bug_this_catches: the refusal oracle must detect the exact-type gap,
    # while both predicates still accept an empty built-in list.
    source = Path(cli.__file__).read_text(encoding='utf-8')
    start = source.index('        return 0 if (')
    end = source.index('\n    if args.operation', start)
    predicates = {
        'exact': "type(result.get('incomplete')) is list",
        'isinstance': "isinstance(result.get('incomplete'), list)",
    }
    for name, predicate in predicates.items():
        replacement = ('        return 0 if (' + predicate + " and not result['incomplete']\n"
                       "                     and result.get('summary', {}).get('coverage_complete') is True\n"
                       "                     and (args.profile != 'full' or "
                       "result['summary'].get('full_gate_pass') is True)) else 1")
        namespace = {'__name__': 'voicelab.harness.incomplete_' + name,
                     '__package__': 'voicelab.harness'}
        exec(compile(source[:start] + replacement + source[end:],
                     '<in-memory-' + name + '-list-control>', 'exec'), namespace)
        main = namespace['main']
        assert invoke(monkeypatch, capsys, profile, [], main=main) == 0
        if name == 'exact':
            assert invoke(monkeypatch, capsys, profile, EmptyListSubclass(), main=main) == 1
        else:
            with pytest.raises(AssertionError):
                assert invoke(monkeypatch, capsys, profile, EmptyListSubclass(), main=main) == 1


@pytest.mark.parametrize('profile,coverage,gate,expected', [
    ('short', True, False, 0), ('full', True, False, 1),
    ('short', False, True, 1), ('full', False, True, 1),
    ('short', 1, True, 1), ('full', True, 1, 1),
])
def test_boolean_coverage_and_resource_gate_preserved(monkeypatch, capsys, profile, coverage, gate, expected):
    assert invoke(monkeypatch, capsys, profile, [], coverage=coverage, gate=gate) == expected


def test_oracle_rejects_unchanged_falsey_predicate(monkeypatch, capsys):
    # Immutable negative control: baseline predicate compiled in memory only.
    source = Path(cli.__file__).read_text(encoding='utf-8')
    start = source.index('        return 0 if (')
    end = source.index('\n    if args.operation', start)
    original = ('        return 0 if (not result.get(\'incomplete\') and '
                'result.get(\'summary\', {}).get(\'coverage_complete\') is True\n'
                '                     and (args.profile != \'full\' or '
                'result[\'summary\'].get(\'full_gate_pass\') is True)) else 1')
    namespace = {'__name__': 'voicelab.harness.incomplete_negative',
                 '__package__': 'voicelab.harness'}
    exec(compile(source[:start] + original + source[end:], '<in-memory-negative>', 'exec'), namespace)
    with pytest.raises(AssertionError):
        assert invoke(monkeypatch, capsys, 'short', None, main=namespace['main']) == 1
