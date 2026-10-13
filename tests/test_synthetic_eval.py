from evals import offline, synthetic


def test_generation_is_deterministic_and_clearly_synthetic():
    a, b = synthetic.generate(), synthetic.generate()
    assert a == b
    assert len(a) == len(synthetic.SLICES) * synthetic.PER_SLICE
    assert len({c['nick'] for c in a}) == len(a)
    assert all(c['nick'].startswith('syn_') for c in a)
    assert {c['label'] for c in a} == {'accept', 'reject'}


def test_cascade_rejects_when_any_stage_rejects():
    cases = [{'nick': 'syn_a', 'slice': 's', 'label': 'reject'},
             {'nick': 'syn_b', 'slice': 's', 'label': 'accept'}]
    rules = {'syn_a': {'decision': 'accept', 'why': 'chef'}, 'syn_b': {'decision': 'accept', 'why': 'chef'}}
    llm = {'syn_a': {'verdict': 'reject'}, 'syn_b': {'verdict': 'unsure'}}
    rows, overall, by_slice = offline.score(cases, rules, llm)
    assert [r['cascade'] for r in rows] == ['reject', 'accept']  # "unsure" is not a reject
    assert overall['cascade']['precision'] == 1.0
    assert by_slice['s']['cascade'] == 2
