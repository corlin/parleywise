import asyncio
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import conversations, db as store, deps, main, model_calls


class Request:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


class Response:
    headers = {"x-litellm-response-cost": "0.001"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"model": "served", "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                           "choices": [{"message": {"content": '{"reply":"hello"}'}}]}).encode()


class ChatModelsTests(unittest.TestCase):
    def test_gateway_models_and_failure(self):
        response = Response()
        response.read = lambda *args: json.dumps({'data': [{'id': 'first'}, {'id': 'second'}, {'id': 'first'}]}).encode()
        with patch.dict('os.environ', {'LITELLM_API_KEY': 'test-key', 'PARLEYWISE_DEFAULT_CHAT_MODEL': 'second'}), \
                patch('urllib.request.urlopen', return_value=response) as urlopen:
            result = main.gateway_chat_models()
            self.assertEqual([m['id'] for m in result], ['first', 'second'])
            self.assertTrue(urlopen.call_args.args[0].full_url.endswith('/v1/models'))
        with patch.dict('os.environ', {'LITELLM_API_KEY': 'test-key'}), \
                patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(HTTPException) as error:
                main.gateway_chat_models()
            self.assertEqual(error.exception.status_code, 502)

    def test_selected_model_reaches_gateway_and_call_record_and_invalid_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'DB_PATH', Path(tmp) / 'test.db'):
            store.init()
            with store.db() as con:
                con.execute("INSERT INTO users(username,display_name,pw_hash,created_at) VALUES ('u','U','',0)")
            cid = conversations.create(1, 'Test')
            models = [{'id': 'default', 'name': 'Default'}, {'id': 'chosen', 'name': 'Chosen'}]
            with patch.object(deps, 'available_chat_models', return_value={'models': models, 'default': 'default'}), \
                    patch.object(deps, 'me', return_value={'id': 1}), \
                    patch.dict('os.environ', {'LITELLM_API_KEY': 'test-key'}), \
                    patch('urllib.request.urlopen', return_value=Response()) as urlopen:
                result = asyncio.run(main.send_conversation_message(cid, Request({'content': 'question', 'model': 'chosen'})))
                sent = json.loads(urlopen.call_args.args[0].data)
                self.assertEqual(sent['model'], 'chosen')
                self.assertEqual(result['messages'][-1]['content'], 'hello')
                rows = model_calls.visible_to(1, conversation_id=cid)
                self.assertEqual((rows[0]['requested_model'], rows[0]['served_model'], rows[0]['cost_usd']),
                                 ('chosen', 'served', 0.001))
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(main.send_conversation_message(cid, Request({'content': 'question', 'model': 'forbidden'})))
                self.assertEqual(error.exception.status_code, 400)
                self.assertEqual(urlopen.call_count, 1)

    def test_admin_selection_filters_gateway_and_only_admin_can_change_it(self):
        models = [{'id': 'one', 'name': 'one'}, {'id': 'two', 'name': 'two'}, {'id': 'three', 'name': 'three'}]
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'DB_PATH', Path(tmp) / 'test.db'):
            store.init()
            request = Request({'allowed': ['two'], 'default': 'two'})
            with patch.object(deps, 'gateway_chat_models', return_value=models), \
                    patch.object(deps, 'me', return_value={'role': 'reviewer'}):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(main.update_admin_chat_models(request))
                self.assertEqual(error.exception.status_code, 403)
            with patch.object(deps, 'gateway_chat_models', return_value=models), \
                    patch.object(deps, 'me', return_value={'role': 'admin'}):
                asyncio.run(main.update_admin_chat_models(request))
                self.assertEqual(main.available_chat_models(), {'models': [models[1]], 'default': 'two'})
                with self.assertRaises(HTTPException):
                    asyncio.run(main.update_admin_chat_models(Request({'allowed': ['outside'], 'default': 'outside'})))
                self.assertEqual(main.chat_model_settings()['allowed'], ['two'])
                with patch.object(deps, 'gateway_chat_models', return_value=[models[0]]):
                    self.assertEqual(main.available_chat_models(), {'models': [], 'default': None})


if __name__ == '__main__':
    unittest.main()
