import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import db as store, memories

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        p = patch.object(store, 'DB_PATH', Path(self.tmp.name) / 'test.db'); p.start(); self.addCleanup(p.stop)
        store.init()
        with store.db() as con:
            con.executemany("INSERT INTO users(username,display_name,pw_hash,created_at) VALUES (?,?,'',0)", [('one','One'),('two','Two')])

    def test_memory_owner_can_list_and_delete_but_other_user_cannot(self):
        mid = memories.create(1, '回答使用中文')
        self.assertEqual(memories.list_for(1)[0]['content'], '回答使用中文')
        self.assertEqual(memories.list_for(2), [])
        self.assertFalse(memories.delete(2, mid))
        self.assertTrue(memories.delete(1, mid))
        self.assertEqual(memories.list_for(1), [])

    def test_owner_can_edit_and_select_only_owned_memories(self):
        first = memories.create(1, '旧偏好')
        second = memories.create(1, '另一条')
        foreign = memories.create(2, '私有')
        self.assertFalse(memories.update(2, first, '越权'))
        self.assertTrue(memories.update(1, first, '新偏好'))
        self.assertEqual([m['content'] for m in memories.selected(1, [first, foreign])], ['新偏好'])
        self.assertEqual(memories.selected(1, []), [])

    def test_project_memory_is_separate_from_personal_memory(self):
        mid = memories.create_project(1, '项目目标：基金合规初审')
        self.assertEqual(memories.list_for(1), [])
        self.assertEqual(memories.list_project()[0]['content'], '项目目标：基金合规初审')
        self.assertEqual(memories.selected_project([mid])[0]['id'], mid)
        self.assertTrue(memories.update_project(mid, '更新后的项目目标'))
        self.assertTrue(memories.delete_project(mid))
