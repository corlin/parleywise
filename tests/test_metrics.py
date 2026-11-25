import os
import tempfile
import unittest

# Keep the real application database untouched when importing the app.
_data_dir = tempfile.TemporaryDirectory()
os.environ['HIAGENT_DATA_DIR'] = _data_dir.name
from app import main
from app.db import db


class MetricsTests(unittest.TestCase):
    def test_even_sample_median_uses_both_middle_values(self):
        with db() as con:
            con.execute('DELETE FROM documents')
            for minutes in (2, 10):
                con.execute("INSERT INTO documents(title, text, arm, status, created_at, review_status, active_ms) "
                            "VALUES (?, ?, 'ai', 'done', 0, 'completed', ?)",
                            ('test', 'text', minutes * 60000))
        self.assertEqual(main.metrics()['arms']['ai']['median_minutes'], 6)


if __name__ == '__main__':
    unittest.main()
