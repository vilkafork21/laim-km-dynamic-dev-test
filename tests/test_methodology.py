"""Проверка audited local drift без сети, с точным FAISS и заданными эмбеддингами."""
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('km_node', ROOT / 'main.py')
node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(node)


def metric():
    return {
        'contract_version': 'laim-monitoring-metric.v2', 'umr_version': 'laim-umr.v2',
        'status': 'computed', 'basket_id': 'synthetic', 'name': 'Доля корректных ответов',
        'score_column': 'main_metric', 'assessment_mode': 'qa',
        'scoring': {'method': 'identity', 'sources': [{
            'source_id': 'source_1', 'column_name': 'main_metric', 'role': 'final_score',
            'normalization': 'numeric', 'polarity': 'direct'}],
            'missing_policy': 'fail', 'majority_denominator': None},
        'aggregation': {'method': 'mean', 'weight_column': None},
        'baseline': {'value': .8, 'scale': 'ratio', 'value_source': 'validation_report',
                     'recomputed_value': .8, 'reported_value': .8, 'reported_scale': 'ratio',
                     'reconciliation': 'match'},
        'primary_validation': {'threshold': None, 'comparator': None, 'scale': 'ratio',
                               'verdict': None, 'affects_monitoring': False},
        'evidence': {},
    }


def frame(texts, scores):
    return pd.DataFrame({'session_id': [f's{i}' for i in range(len(texts))],
                         'query_id': [f'q{i}' for i in range(len(texts))],
                         'input_query': texts, 'output_answer': ['Ответ'] * len(texts),
                         'input_query_count': [1] * len(texts), 'main_metric': scores})


def check(scores, baseline=.8, accuracy=.74, policy='exclude_unit', mode='qa', weighted=False):
    payload = metric()
    payload['baseline']['value'] = baseline
    payload['scoring']['missing_policy'] = policy
    payload['assessment_mode'] = mode
    data = frame(['Вопрос'] * len(scores), scores)
    if mode == 'dialogue':
        data['reference_group_id'] = [f'd{i // 2}' for i in range(len(scores))]
        data['turn_index'] = [i % 2 + 1 for i in range(len(scores))]
    if weighted:
        payload['aggregation'] = {'method': 'frequency_weighted_mean', 'weight_column': 'input_query_count'}
        data['input_query_count'] = [1, 3]
    return node.main(accuracy, payload, data)['all_results']


def test_zero_baseline_missing_and_invalid_scores_are_gray():
    for kwargs in [dict(scores=[0.], baseline=0), dict(scores=[None]),
                   dict(scores=[1., None, None]), dict(scores=[1.], accuracy=.59)]:
        result = check(**kwargs)
        assert result['color'] == 'gray' and result['status'] == 'not_computable'
    assert check([.8], accuracy=None)['color'] == 'green'
    for policy in ['zero', 'exclude_unit', 'fail']:
        assert check([1., None, None], policy=policy)['color'] == 'gray'
    assert check([.8] * 4 + [None])['color'] == 'green'


def test_relative_delta_weighting_and_dialogue_mean():
    result = check([.607] * 10, baseline=.69)
    assert np.isclose(result['km_delta'], (.69 - .607) / .69)
    assert result['color'] == 'green'
    assert np.isclose(check([0., 1.], weighted=True)['km_monitoring'], .75)
    assert np.isclose(check([.6, 1.], mode='dialogue')['km_monitoring'], .8)
    for score, color in [(.85, 'green'), (.84, 'amber'), (.75, 'red')]:
        assert check([score], baseline=1.)['color'] == color


def test_dialogue_missing_policies_and_actual_unit_count():
    assert check([.6, 1.], mode='dialogue')['coverage']['total_units'] == 1
    for policy in ['fail', 'exclude_unit']:
        assert check([1., None], mode='dialogue', policy=policy)['color'] == 'gray'
    assert check([1., None], mode='dialogue', policy='zero')['km_monitoring'] == .5
    assert check([1., None], mode='dialogue', policy='exclude_value')['km_monitoring'] == 1.
