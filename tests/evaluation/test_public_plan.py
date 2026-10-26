"""Independent public-dev schedule oracles; no fixture files or hidden inputs."""
from collections import Counter
from copy import deepcopy

import pytest

from voicelab.evaluation.artifacts import digest
from voicelab.evaluation.planning import create_trial_plan, validate_trial_plan


def request():
    families = [f'public-family-{i}' for i in range(8)]
    speakers = ['public-speaker-a', 'public-speaker-b']
    return {'run_id': 'public-dev-plan', 'seed': 20261025, 'families': families,
            'speakers': speakers, 'network_profile': 'offline-loopback',
            'models': {'V': 'mock-v', 'C': 'mock-c', 'K': 'mock-k'},
            'fixture_cells': [{'family': f, 'speaker': s, 'fixture_id': f'{f}-{s}'}
                              for f in families for s in speakers],
            'fault_schedules': {f: [{'kind': 'disconnect', 'turn_position': 4}]
                                for f in families}, 'turns_per_task': 5}


def test_schedule_balance_cells_controls_and_unique_turns():
    """what_bug_this_catches: omitted cells, K entering paired counts or duplicate IDs."""
    plan = create_trial_plan(request())
    validate_trial_plan(plan)
    assert len(plan['pairs']) == 48
    assert len(plan['tasks']) == 144
    assert len(set(plan['scheduled_turns'])) == 720
    clean = [p for p in plan['pairs'] if p['cohort'] == 'clean']
    fault = [p for p in plan['pairs'] if p['cohort'] == 'fault']
    assert Counter(p['order'] for p in clean) == {'AB': 20, 'BA': 20}
    assert Counter(p['order'] for p in fault) == {'AB': 4, 'BA': 4}
    assert len(Counter((p['family'], p['speaker']) for p in clean)) == 16
    assert min(Counter((p['family'], p['speaker']) for p in clean).values()) == 2
    assert Counter(p['speaker'] for p in fault) == {'public-speaker-a': 4, 'public-speaker-b': 4}
    for pair in plan['pairs']:
        tasks = [t for t in plan['tasks'] if t['pair_id'] == pair['pair_id']]
        assert [t['arm'] for t in tasks] == (['V', 'C', 'K'] if pair['order'] == 'AB' else ['C', 'V', 'K'])
        assert all(t['fixture_id'] == pair['fixture_id'] and t['fault_schedule'] == pair['fault_schedule'] for t in tasks)
    assert all(t['pair_id'] is None for t in plan['turn_assignments'] if t['arm'] == 'K')


@pytest.mark.parametrize('mutation', ['order', 'membership', 'counts'])
def test_rehashing_tampered_schedule_does_not_authorize_it(mutation):
    """what_bug_this_catches: a valid checksum blessing a different experimental design."""
    plan = deepcopy(create_trial_plan(request()))
    if mutation == 'order':
        plan['tasks'][0], plan['tasks'][1] = plan['tasks'][1], plan['tasks'][0]
    elif mutation == 'membership':
        plan['pairs'][0]['speaker'] = 'unplanned-speaker'
    else:
        plan['scheduled_turns'].pop()
    plan['plan_hash'] = digest({k: v for k, v in plan.items() if k != 'plan_hash'})
    with pytest.raises(ValueError, match='prespecified balanced design'):
        validate_trial_plan(plan)


def test_schedule_reproducible_and_contains_no_answer_or_claim():
    """what_bug_this_catches: nondeterminism or expected answers leaking into task plans."""
    one, two = create_trial_plan(request()), create_trial_plan(request())
    assert one == two
    assert one['evidence_status'] == 'prespecified schedule; no execution or acceptance'
    for task in one['tasks']:
        assert not ({'expected_answer', 'oracle', 'transcript', 'spoken_correct'} & set(task))


@pytest.mark.parametrize('field,value', [('seed', True), ('turns_per_task', 9), ('turns_per_task', 0)])
def test_schedule_refuses_invalid_bounds(field, value):
    """what_bug_this_catches: bool seeds or over-horizon tasks silently accepted."""
    spec = request()
    spec[field] = value
    with pytest.raises(ValueError):
        create_trial_plan(spec)
