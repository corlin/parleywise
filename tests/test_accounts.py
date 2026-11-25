import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import auth
from app import db as store


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = patch.object(store, 'DB_PATH', Path(self.tmp.name) / 'test.db')
        self.path.start()
        self.addCleanup(self.path.stop)
        store.init()

    def test_invited_registration_creates_reviewer_and_enforces_unique_username(self):
        user = auth.register('alice', 'Alice', 'longpassword')
        self.assertEqual(user['role'], 'reviewer')
        token, logged_in = auth.login('alice', 'longpassword')
        self.assertTrue(token)
        self.assertEqual(logged_in['id'], user['id'])
        with self.assertRaises(ValueError):
            auth.register('alice', 'Other', 'longpassword')

    def test_profile_and_password_change_invalidate_old_sessions(self):
        user = auth.register('alice', 'Alice', 'longpassword')
        token, _ = auth.login('alice', 'longpassword')
        updated = auth.update_profile(user['id'], 'New name')
        self.assertEqual(updated['name'], 'New name')
        with self.assertRaises(ValueError):
            auth.change_password(user['id'], 'wrong', 'newpassword')
        auth.change_password(user['id'], 'longpassword', 'newpassword')
        self.assertIsNone(auth.user_for(token))
        self.assertTrue(auth.login('alice', 'newpassword')[0])


if __name__ == '__main__':
    unittest.main()
