import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import review
from app import db as store
from app import model_calls
from app import conversations


class Response:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.payload).encode()


class ModelCallTests(unittest.TestCase):
    def test_attempts_record_retries_usage_and_unknown_cost_without_content(self):
        records = []
        client = review.LLM(observer=records.append)
        response = Response({'model': 'served-model', 'usage': {'prompt_tokens': 12, 'completion_tokens': 4},
                             'choices': [{'message': {'content': 'private output'}}]})
        with patch('urllib.request.urlopen', side_effect=[urllib.error.URLError('private error'), response]), patch('time.sleep'):
            self.assertEqual(client._call('alias', 'secret prompt', 'private input'), 'private output')
        self.assertEqual([r['status'] for r in records], ['failed', 'responded'])
        self.assertEqual([r['attempt'] for r in records], [1, 2])
        self.assertEqual(records[1]['prompt_tokens'], 12)
        self.assertIsNone(records[1]['cost_usd'])
        self.assertEqual(records[1]['served_model'], 'served-model')
        self.assertNotIn('private', json.dumps(records))
        self.assertNotIn('secret', json.dumps(records))

    def test_records_scope_private_conversations_and_shared_reviews(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'DB_PATH', Path(tmp) / 'test.db'):
            store.init()
            model_calls.record({'status': 'responded', 'requested_model': 'm'}, user_id=1, kind='chat', conversation_id=10)
            model_calls.record({'status': 'responded', 'requested_model': 'm'}, user_id=2, kind='chat', conversation_id=20)
            model_calls.record({'status': 'failed', 'requested_model': 'm'}, user_id=1, kind='review', document_id=3)
            rows = model_calls.visible_to(1)
            self.assertEqual(len(rows), 2)
            self.assertFalse(any(r['conversation_id'] == 20 for r in rows))
            self.assertEqual(len(model_calls.visible_to(2)), 2)

    def test_deleting_conversation_removes_its_model_call_records(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'DB_PATH', Path(tmp) / 'test.db'):
            store.init()
            with store.db() as con:
                con.execute("INSERT INTO users(username,display_name,pw_hash,created_at) VALUES ('u','U','',0)")
            cid = conversations.create(1, 'Test')
            model_calls.record({'status': 'responded', 'requested_model': 'm'}, user_id=1, kind='chat', conversation_id=cid)
            self.assertFalse(conversations.delete(2, cid))
            self.assertEqual(len(model_calls.visible_to(1)), 1)
            self.assertTrue(conversations.delete(1, cid))
            self.assertEqual(model_calls.visible_to(1), [])
