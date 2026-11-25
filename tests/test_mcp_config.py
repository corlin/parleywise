import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import conversations, db as store, deps, mcp_config
from app.routers import chat_routes


class _Req:
    def __init__(self, body=None):
        self._body = body or {}

    async def json(self):
        return self._body


class McpConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(store, 'DB_PATH', Path(self.tmp.name) / 'test.db')
        p.start()
        self.addCleanup(p.stop)
        store.init()
        with store.db() as con:
            con.executemany(
                "INSERT INTO users(username,display_name,pw_hash,role,created_at) VALUES (?,?,'',?,0)",
                [('one', 'One', 'admin'), ('two', 'Two', 'reviewer')],
            )

    def test_configuration_is_private_and_can_be_deleted(self):
        mid = mcp_config.create(1, 'Example', 'https://example.com/mcp')
        self.assertEqual(mcp_config.list_for(1)[0]['name'], 'Example')
        self.assertEqual(mcp_config.list_for(2), [])
        self.assertFalse(mcp_config.delete(2, mid))
        self.assertTrue(mcp_config.delete(1, mid))

    def test_stdio_is_admin_only_and_workspace_shared(self):
        with patch.object(deps, 'me', return_value={'id': 2, 'role': 'reviewer'}):
            with self.assertRaises(HTTPException) as err:
                asyncio.run(chat_routes.create_mcp_server(
                    _Req({'name': 'bad', 'transport': 'stdio', 'command': 'echo'})
                ))
            self.assertEqual(err.exception.status_code, 403)

        with patch.object(deps, 'me', return_value={'id': 1, 'role': 'admin'}):
            res = asyncio.run(chat_routes.add_preset_compliance_mcp(_Req()))
            sid = res['id']
            self.assertEqual({t['name'] for t in res['tools']}, {'list_rule_sources', 'search_rules', 'get_rule'})
            # 幂等：重复添加返回同一条配置
            res2 = asyncio.run(chat_routes.add_preset_compliance_mcp(_Req()))
            self.assertEqual(res2['id'], sid)
        # 非管理员可见工作区共享 MCP，但不能删除
        self.assertEqual(len(mcp_config.list_for(2)), 1)
        self.assertFalse(mcp_config.delete(2, sid, is_admin=False))

    def test_http_mcp_blocks_private_and_loopback_urls(self):
        with patch.object(deps, 'me', return_value={'id': 2, 'role': 'reviewer'}):
            for bad_url in ('http://127.0.0.1:8000/mcp', 'http://localhost:9000/mcp', 'http://192.168.1.10/mcp'):
                with self.assertRaises(HTTPException) as err:
                    asyncio.run(chat_routes.create_mcp_server(_Req({'name': 'ssrf', 'url': bad_url})))
                self.assertEqual(err.exception.status_code, 400)

    def test_chat_executes_mcp_tool_call_and_appends_trace(self):
        with patch.object(deps, 'me', return_value={'id': 1, 'role': 'admin'}):
            sid = asyncio.run(chat_routes.add_preset_compliance_mcp(_Req()))['id']
        cid = conversations.create(1, 'MCP Test')
        calls = []

        testcase = self

        def fake_send(_llm, model, body):
            payload = json.loads(body)
            calls.append(payload)
            if len(calls) == 1:
                return {
                    'content': '',
                    'tool_calls': [{
                        'id': 'tc_1',
                        'type': 'function',
                        'function': {'name': f'mcp_{sid}__get_rule', 'arguments': '{"rule_id":"R18"}'},
                    }],
                }
            tool_msgs = [m for m in payload['messages'] if m.get('role') == 'tool']
            testcase.assertIn('第十五条（一）项', tool_msgs[0]['content'])
            return {'content': json.dumps({'reply': 'R18 针对无依据的优越性用语。'}, ensure_ascii=False)}

        with patch.object(deps, 'me', return_value={'id': 1, 'role': 'admin'}), \
                patch.object(deps, 'available_chat_models', return_value={'models': [{'id': 'm1', 'name': 'm1'}], 'default': 'm1'}), \
                patch.dict('os.environ', {'LITELLM_API_KEY': 'test-key'}), \
                patch.object(chat_routes.review.LLM, '_send_request', fake_send):
            convo = asyncio.run(chat_routes.send_conversation_message(
                cid, _Req({'content': '查一下 R18', 'model': 'm1', 'mcp_server_ids': [sid]})
            ))
        last = convo['messages'][-1]['content']
        self.assertIn('R18 针对无依据的优越性用语。', last)
        self.assertIn('已调用工具：合规规则检索（本地）.get_rule', last)
