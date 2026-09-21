import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, InvalidStatus
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('web_bridge', ROOT / 'bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.token_path = Path(self.tmp.name) / 'token'
        self.token_patch = patch.object(bridge, 'TOKEN_FILE', self.token_path)
        self.token_patch.start()
        bridge.clients.clear()
        bridge.jobs.clear()
        self.secret = bridge.token()
        self.server = await serve(bridge.handle, '127.0.0.1', 0,
            origins=[None, re.compile(r'chrome-extension://[a-p]{32}')])
        self.port = self.server.sockets[0].getsockname()[1]
        self.uri = f'ws://127.0.0.1:{self.port}'
        self.sockets = []

    async def asyncTearDown(self):
        for ws in self.sockets:
            await ws.close()
        self.server.close()
        await self.server.wait_closed()
        self.token_patch.stop()
        self.tmp.cleanup()

    async def client(self, role='cursor', secret=None, **kwargs):
        ws = await connect(self.uri, **kwargs)
        self.sockets.append(ws)
        await ws.send(json.dumps({'role': role, 'token': self.secret if secret is None else secret}))
        self.assertEqual(json.loads(await ws.recv()), {'ok': True})
        return ws

    async def request(self, ws, **payload):
        await ws.send(json.dumps(payload))
        return json.loads(await asyncio.wait_for(ws.recv(), 3))

    async def browser(self):
        ws = await self.client('extension')
        await ws.send(json.dumps({'type': 'sessions', 'sessions': [{'id': 'session-1', 'key': '/c/1'}]}))
        # Synchronize on server state, not a fixed sleep.
        async with asyncio.timeout(3):
            while not any(bridge.clients.values()):
                await asyncio.sleep(.001)
        return ws

    async def submit(self):
        browser = await self.browser()
        cursor = await self.client()
        job = await self.request(cursor, type='send', session_id='session-1', prompt='你好，修改建议？')
        dispatch = json.loads(await browser.recv())
        self.assertEqual(dispatch['job_id'], job['job_id'])
        return browser, cursor, job

    async def test_token_persistence_and_permissions(self):
        self.assertEqual(self.secret, bridge.token())
        self.assertGreaterEqual(len(self.secret), 40)
        self.assertEqual(self.token_path.stat().st_mode & 0o777, 0o600)

    async def test_bad_token_rejected(self):
        with self.assertRaises(ConnectionClosed):
            await self.client(secret='incorrect')

    async def test_invalid_role_rejected(self):
        with self.assertRaises(ConnectionClosed):
            await self.client(role='unknown')

    async def test_web_origin_rejected(self):
        with self.assertRaises(InvalidStatus):
            await connect(self.uri, origin='https://example.com')

    async def test_extension_origin_allowed(self):
        await self.client('extension', origin='chrome-extension://' + 'a' * 32)

    async def test_second_browser_rejected_without_affecting_first(self):
        await self.browser()
        with self.assertRaises(ConnectionClosed):
            await self.client('extension')
        cursor = await self.client()
        self.assertEqual(len((await self.request(cursor, type='list'))['sessions']), 1)

    async def test_roundtrip_and_duplicate_result(self):
        browser, cursor, job = await self.submit()
        result = dict(type='result', job_id=job['job_id'], text='建议：新增函数。', diagnostics={'sawHidden': True, 'version': '0.2.0'})
        await browser.send(json.dumps(result))
        async with asyncio.timeout(3):
            while bridge.jobs[job['job_id']]['status'] == 'running':
                await asyncio.sleep(.001)
        await browser.send(json.dumps({**result, 'text': 'duplicate'}))
        answer = await self.request(cursor, type='get', job_id=job['job_id'])
        self.assertEqual(answer['status'], 'completed')
        self.assertEqual(answer['result'], '建议：新增函数。')
        self.assertNotIn('owner', answer)
        self.assertTrue(answer['diagnostics']['sawHidden'])

    async def test_busy_session(self):
        _, cursor, _ = await self.submit()
        self.assertIn('busy', (await self.request(cursor, type='send', session_id='session-1', prompt='again'))['error'])

    async def test_invalid_prompts(self):
        cursor = await self.client()
        for prompt in ('', ' ', None, 123, 'x' * 60001):
            self.assertIn('error', await self.request(cursor, type='send', session_id='session-1', prompt=prompt))

    async def test_unknown_session_and_job(self):
        cursor = await self.client()
        self.assertIn('error', await self.request(cursor, type='send', session_id='missing', prompt='hi'))
        self.assertIn('error', await self.request(cursor, type='get', job_id='missing'))

    async def test_cursor_cannot_forge_result(self):
        _, cursor, job = await self.submit()
        self.assertIn('error', await self.request(cursor, type='result', job_id=job['job_id'], text='fake'))
        self.assertEqual(bridge.jobs[job['job_id']]['status'], 'running')

    async def test_disconnect_marks_pending_error(self):
        browser, cursor, job = await self.submit()
        await browser.close()
        async with asyncio.timeout(3):
            while bridge.jobs[job['job_id']]['status'] == 'running':
                await asyncio.sleep(.001)
        self.assertEqual((await self.request(cursor, type='get', job_id=job['job_id']))['status'], 'error')

    async def test_expiry_and_late_result(self):
        browser, cursor, job = await self.submit()
        bridge.jobs[job['job_id']]['created'] -= 301
        result = await self.request(cursor, type='get', job_id=job['job_id'])
        self.assertEqual(result['status'], 'error')
        await browser.send(json.dumps({'type': 'result', 'job_id': job['job_id'], 'text': 'late'}))
        self.assertEqual((await self.request(cursor, type='get', job_id=job['job_id']))['status'], 'error')

    async def test_mcp_stdio_full_roundtrip(self):
        browser = await self.browser()
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'cursor_mcp.py')],
            env={**os.environ, 'CURSOR_WEB_PORT': str(self.port), 'CURSOR_WEB_TOKEN_FILE': str(self.token_path)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                self.assertEqual({t.name for t in tools.tools}, {'web_chat_list_sessions', 'web_chat_send', 'web_chat_get_result'})
                listed = await session.call_tool('web_chat_list_sessions', {})
                self.assertEqual(json.loads(listed.content[0].text)['sessions'][0]['id'], 'session-1')
                sent = await session.call_tool('web_chat_send', {'session_id': 'session-1', 'prompt': 'code suggestion'})
                jid = json.loads(sent.content[0].text)['job_id']
                self.assertEqual(json.loads(await browser.recv())['job_id'], jid)
                await browser.send(json.dumps({'type': 'result', 'job_id': jid, 'text': 'answer from browser'}))
                received = await session.call_tool('web_chat_get_result', {'job_id': jid})
                self.assertEqual(json.loads(received.content[0].text)['result'], 'answer from browser')


    async def test_model_endpoint_real_http_bridge_tool_loop(self):
        import socket
        import httpx
        import uvicorn
        spec = importlib.util.spec_from_file_location('endpoint_live', ROOT / 'model_endpoint.py')
        endpoint = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(endpoint)
        browser = await self.browser()
        env = patch.dict(os.environ, {'CURSOR_WEB_PORT': str(self.port),
                                     'CURSOR_WEB_TOKEN_FILE': str(self.token_path)})
        env.start()
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(endpoint.create_app('test-key', 'session-1', poll_interval=.001),
                                              log_level='error', access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[sock]))

        async def webpage():
            for turn in range(2):
                dispatch = json.loads(await browser.recv())
                self.assertEqual(dispatch.get('response_format'), 'json_code_block')
                request = json.loads(dispatch['prompt'].split('CURRENT_REQUEST:\n')[1])
                if turn == 0:
                    answer = {'request_id': request['request_id'], 'content': None,
                              'tool_calls': [{'name': 'read_file', 'arguments': {'path': 'demo.js'}}]}
                else:
                    self.assertEqual(request['messages'][-1]['content'], 'function demo() {}')
                    answer = {'request_id': request['request_id'], 'content': 'Reviewed actual tool result', 'tool_calls': []}
                await browser.send(json.dumps({'type': 'result', 'job_id': dispatch['job_id'],
                                               'text': json.dumps(answer)}))
        worker = asyncio.create_task(webpage())
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if serving.done():
                        await serving
                        self.fail('HTTP server did not start')
                    await asyncio.sleep(.001)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',
                                         headers={'Authorization': 'Bearer test-key'}, timeout=5) as client:
                body = {'model': 'web-ai', 'messages': [{'role': 'user', 'content': 'Review demo.js'}],
                        'tools': [{'type': 'function', 'function': {'name': 'read_file',
                                  'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}]}
                first = await client.post('/v1/chat/completions', json=body)
                self.assertEqual(first.status_code, 200, first.text)
                message = first.json()['choices'][0]['message']
                self.assertEqual(message['tool_calls'][0]['function']['name'], 'read_file')
                body['messages'] += [message, {'role': 'tool', 'tool_call_id': message['tool_calls'][0]['id'],
                                               'content': 'function demo() {}'}]
                body['stream'] = True
                second = await client.post('/v1/chat/completions', json=body)
                self.assertEqual(second.status_code, 200)
                self.assertIn('Reviewed actual tool result', second.text)
                self.assertIn('data: [DONE]', second.text)
                await worker
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            server.should_exit = True
            await serving
            sock.close()
            env.stop()


    async def test_bridge_provider_budget_above_old_limit_and_utf16_guard(self):
        browser = await self.browser()
        next(iter(bridge.clients.values()))[0]['provider'] = 'deepseek'
        cursor = await self.client()
        bad = await self.request(cursor, type='send', session_id='session-1', prompt='😀' * 80001)
        self.assertIn('limit=160000', bad['error'])
        self.assertEqual(len(bridge.jobs), 0)
        prompt = 'x' * 90000
        job = await self.request(cursor, type='send', session_id='session-1', prompt=prompt)
        self.assertEqual(job['status'], 'running')
        dispatched = json.loads(await browser.recv())
        self.assertEqual(dispatched['prompt'], prompt)
