import os
import tempfile
import unittest

# Keep the real application database untouched when importing the app.
_data_dir = tempfile.TemporaryDirectory()
os.environ['PARLEYWISE_DATA_DIR'] = _data_dir.name
from app import main
from app.db import db
from app.routers import review_routes


class MetricsTests(unittest.TestCase):
    def test_even_sample_median_uses_both_middle_values(self):
        with db() as con:
            con.execute('DELETE FROM documents')
            for minutes in (2, 10):
                con.execute("INSERT INTO documents(title, text, arm, status, created_at, review_status, active_ms) "
                            "VALUES (?, ?, 'ai', 'done', 0, 'completed', ?)",
                            ('test', 'text', minutes * 60000))
        self.assertEqual(main.metrics()['arms']['ai']['median_minutes'], 6)

    def test_metrics_cost_skips_unknown_and_golden_includes_real_cases(self):
        import json
        with db() as con:
            con.execute('DELETE FROM documents')
            con.execute(
                "INSERT INTO documents(title, text, arm, status, created_at, review_status, active_ms, usage_json) "
                "VALUES ('a', 't', 'ai', 'done', 0, 'completed', 60000, ?)",
                (json.dumps({'prompt_tokens': 100, 'completion_tokens': 50, 'cost_usd': 0.02, 'calls_without_cost': 0}),),
            )
            con.execute(
                "INSERT INTO documents(title, text, arm, status, created_at, review_status, active_ms, usage_json) "
                "VALUES ('b', 't', 'ai', 'done', 0, 'completed', 60000, ?)",
                (json.dumps({'prompt_tokens': 200, 'completion_tokens': 100, 'cost_usd': 0.0, 'calls_without_cost': 1}),),
            )
        ai = main.metrics()['arms']['ai']
        self.assertEqual(ai['avg_tokens'], 225)
        self.assertEqual(ai['avg_cost_usd'], 0.02)
        self.assertEqual(ai['known_cost_docs'], 1)
        golden = review_routes.list_golden()
        datasets = {g['dataset'] for g in golden}
        self.assertIn('real', datasets)
        self.assertIn('synthetic', datasets)


if __name__ == '__main__':
    unittest.main()
