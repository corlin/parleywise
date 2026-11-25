import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import db as store, mcp_config

class McpConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        p = patch.object(store, 'DB_PATH', Path(self.tmp.name) / 'test.db'); p.start(); self.addCleanup(p.stop)
        store.init()
        with store.db() as con:
            con.executemany("INSERT INTO users(username,display_name,pw_hash,created_at) VALUES (?,?,'',0)", [('one','One'),('two','Two')])
    def test_configuration_is_private_and_can_be_deleted(self):
        mid = mcp_config.create(1, 'Example', 'https://example.com/mcp')
        self.assertEqual(mcp_config.list_for(1)[0]['name'], 'Example')
        self.assertEqual(mcp_config.list_for(2), [])
        self.assertFalse(mcp_config.delete(2, mid))
        self.assertTrue(mcp_config.delete(1, mid))
