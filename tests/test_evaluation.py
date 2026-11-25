import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import review
import compare_evals


class EvaluationTests(unittest.TestCase):
    def test_empty_denominator_is_unknown_not_perfect(self):
        result = review.evaluate([({'id': 'N', 'text': 'clean', 'expected': []}, [])], {})
        self.assertIsNone(result['recall'])
        self.assertIsNone(result['precision_strict'])
        self.assertIsNone(result['snippet_precision'])

    def test_comparison_requires_matching_inputs_and_no_failed_cases(self):
        manifest = {'dataset_sha256': 'abc', 'rules_sha256': 'rules', 'code_sha256': 'code',
                    'effective_date': '2026-11-24', 'cheap_model': 'small', 'strong_model': 'large', 'offline': False}
        base = {'manifest': manifest, 'failed_cases': [], 'cases': 2, 'completed_cases': 2, 'recall': 0.5, 'precision_strict': 0.5,
                'precision_lenient': 0.5, 'snippet_precision': 0.5, 'seconds': 10, 'tokens_per_doc': 50,
                'cost_per_doc_usd': None, 'mode': 'baseline'}
        candidate = {**base, 'mode': 'collaboration', 'recall': 1.0}
        result = compare_evals.compare(base, candidate)
        self.assertEqual(result['metrics']['recall']['change'], 0.5)
        self.assertIsNone(result['metrics']['cost_per_doc_usd']['change'])
        with self.assertRaisesRegex(ValueError, '失败'):
            compare_evals.compare(base, {**candidate, 'failed_cases': ['C01']})
        with self.assertRaisesRegex(ValueError, 'dataset_sha256'):
            compare_evals.compare(base, {**candidate, 'manifest': {**manifest, 'dataset_sha256': 'other'}})
        with self.assertRaisesRegex(ValueError, '未完成'):
            compare_evals.compare(base, {**candidate, 'completed_cases': 1})
        with self.assertRaisesRegex(ValueError, '缺少'):
            compare_evals.compare({}, candidate)

    def test_failed_case_is_not_reported_as_a_clean_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden = root / 'cases.jsonl'
            golden.write_text(json.dumps({'id': 'C01', 'text': '内容', 'expected': [{'rule_id': 'R01'}]}, ensure_ascii=False))
            argv = ['review.py', '--golden', str(golden), '--eval', '--offline', '--out', str(root / 'out')]
            with patch('sys.argv', argv), patch.object(review, 'review_doc', side_effect=RuntimeError('模拟失败')), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    review.main()
            self.assertEqual(failure.exception.code, 1)
            result = json.loads((root / 'out' / 'eval.json').read_text())
            self.assertEqual(result['failed_cases'], ['C01'])
            self.assertEqual(result['completed_cases'], 0)
            self.assertIsNone(result['recall'])
            self.assertIn('处理失败', (root / 'out' / 'C01.md').read_text())
            self.assertEqual(json.loads((root / 'out' / 'C01.json').read_text())['status'], 'failed')
            self.assertIn('dataset_sha256', result['manifest'])

    def test_completed_runs_can_be_compared_with_matching_manifest(self):
        class FakeLLM:
            key = 'test'
            usage = {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'cost_usd': 0.0, 'calls_without_cost': 0}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden = root / 'cases.jsonl'
            golden.write_text(json.dumps({'id': 'N01', 'text': '合规样例', 'expected': []}, ensure_ascii=False))
            reports = []
            for mode in ('baseline', 'collaboration'):
                argv = ['review.py', '--golden', str(golden), '--eval', '--out', str(root / mode)]
                if mode == 'collaboration':
                    argv.append('--collaboration')
                with patch('sys.argv', argv), patch.object(review, 'LLM', FakeLLM), patch.object(review, 'load_dotenv'), patch.object(review, 'review_doc', return_value=([], [])), patch.dict('os.environ', {'PARLEYWISE_TODAY': '2026-11-24', 'PARLEYWISE_MODEL_CHEAP': 'small', 'PARLEYWISE_MODEL_STRONG': 'large'}), contextlib.redirect_stdout(io.StringIO()):
                    review.main()
                reports.append(json.loads((root / mode / 'eval.json').read_text()))
            self.assertEqual(compare_evals.compare(*reports)['cases'], 1)
            self.assertIsNone(reports[0]['recall'])
