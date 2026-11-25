#!/usr/bin/env python3
"""比较两份可追溯的评测结果；不推断统计显著性或业务合规性。"""
import argparse
import json
import sys
from pathlib import Path

FIELDS = ('dataset_sha256', 'rules_sha256', 'code_sha256', 'effective_date', 'cheap_model', 'strong_model', 'offline')
METRICS = ('recall', 'precision_strict', 'precision_lenient', 'snippet_precision', 'snippet_count_per_doc',
           'rule_hits_per_doc', 'seconds', 'tokens_per_doc', 'cost_per_doc_usd')


def compare(baseline, candidate):
    for report in (baseline, candidate):
        if not isinstance(report.get('manifest'), dict) or any(k not in report['manifest'] for k in FIELDS):
            raise ValueError('缺少评测清单，历史结果不能直接自动对照')
        if 'failed_cases' not in report or 'cases' not in report or 'completed_cases' not in report or not report['cases']:
            raise ValueError('缺少样例数量或失败记录')
        if report['failed_cases']:
            raise ValueError('存在失败样例，不能作为完整评测对照')
        if report['completed_cases'] != report['cases']:
            raise ValueError('存在未完成样例，不能作为完整评测对照')
    for key in FIELDS:
        if baseline['manifest'][key] != candidate['manifest'][key]:
            raise ValueError(f'评测条件不一致：{key}')
    if baseline['cases'] != candidate['cases']:
        raise ValueError('评测条件不一致：样例数量')
    metrics = {}
    for name in METRICS:
        a, b = baseline.get(name), candidate.get(name)
        delta = round(b - a, 5) if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool) else None
        metrics[name] = {'baseline': a, 'candidate': b, 'change': delta}
    return {'baseline_mode': baseline.get('mode'), 'candidate_mode': candidate.get('mode'),
            'cases': baseline['cases'], 'manifest': baseline['manifest'], 'metrics': metrics,
            'note': '单次对照仅供观察；不代表统计显著性、真实业务效果或合规结论。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path, help='基线 eval.json')
    parser.add_argument('candidate', type=Path, help='候选 eval.json')
    args = parser.parse_args()
    try:
        result = compare(json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text()))
    except (OSError, ValueError, TypeError) as e:
        print(f'无法比较：{e}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
