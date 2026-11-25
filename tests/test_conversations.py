import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import db as store
from app import conversations


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = patch.object(store, 'DB_PATH', Path(self.tmp.name) / 'test.db')
        path.start()
        self.addCleanup(path.stop)
        store.init()
        with store.db() as con:
            con.executemany("INSERT INTO users(username, display_name, pw_hash, created_at) VALUES (?, ?, '', 0)",
                            [('one', 'One'), ('two', 'Two')])

    def test_conversation_and_messages_are_scoped_to_owner(self):
        cid = conversations.create(1, 'Test')
        conversations.add_message(1, cid, 'user', 'Hello')
        self.assertEqual(conversations.get(1, cid)['messages'][0]['content'], 'Hello')
        self.assertIsNone(conversations.get(2, cid))
        self.assertFalse(conversations.add_message(2, cid, 'user', 'intrusion'))
        self.assertEqual(conversations.list_for(2), [])

    def test_summary_does_not_delete_original_messages(self):
        cid = conversations.create(1, 'Test')
        conversations.add_message(1, cid, 'user', 'Original')
        self.assertTrue(conversations.set_summary(1, cid, 'Summary'))
        self.assertEqual(conversations.get(1, cid)['messages'][0]['content'], 'Original')
        self.assertFalse(conversations.set_summary(2, cid, 'intrusion'))

    def test_compression_batch_leaves_excess_messages_for_later(self):
        messages = [{'id': i, 'content': '测' * 8000} for i in range(1, 6)]
        batch = conversations.compression_batch('', messages, 20000)
        self.assertEqual([m['id'] for m in batch], [1, 2])
        self.assertEqual(len(messages), 5)

    def test_edit_summary_preserves_coverage_and_owner(self):
        cid = conversations.create(1, 'Test')
        conversations.add_message(1, cid, 'user', 'Original')
        mid = conversations.get(1, cid)['messages'][0]['id']
        conversations.set_summary(1, cid, 'Old', mid)
        self.assertFalse(conversations.edit_summary(2, cid, 'intrusion', 'Old', mid))
        self.assertTrue(conversations.edit_summary(1, cid, 'Corrected', 'Old', mid))
        self.assertFalse(conversations.edit_summary(1, cid, 'Stale', 'Old', mid))
        doc = conversations.get(1, cid)
        self.assertEqual((doc['summary'], doc['summary_through']), ('Corrected', mid))

    def test_exchange_is_saved_as_a_pair_only_for_owner(self):
        cid = conversations.create(1, 'Test')
        self.assertFalse(conversations.add_exchange(2, cid, 'question', 'answer'))
        self.assertEqual(conversations.get(1, cid)['messages'], [])
        self.assertTrue(conversations.add_exchange(1, cid, 'question', 'answer'))
        self.assertEqual([m['content'] for m in conversations.get(1, cid)['messages']], ['question', 'answer'])


if __name__ == '__main__':
    unittest.main()
