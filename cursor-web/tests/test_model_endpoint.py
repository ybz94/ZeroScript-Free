import asyncio
import importlib.util
import json
from pathlib import Path
import unittest
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('model_endpoint', ROOT / 'model_endpoint.py')
endpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(endpoint)

TOOL = {'type': 'function', 'function': {'name': 'read_file', 'parameters': {
    'type': 'object', 'properties': {'path': {'type': 'string'}},
    'required': ['path'], 'additionalProperties': False}}}
BODY = {'model': 'web-ai', 'messages': [{'role': 'user', 'content': 'Help with code'}]}
HEADERS = {'Authorization': 'Bearer local-test-key'}


class FakeWeb:
    def __init__(self):
        self.sent = []
        self.result = None
        self.failure = None
        self.waiting = False
        self.mode = 'text'
        self.provider = 'unknown'
        self.escape_mode = None
        self.repair_padding = ''
        self.bad_repair_schema = False

    async def __call__(self, payload):
        if payload['type'] == 'list':
            return {'sessions': [{'id': 'bound-page', 'provider': self.provider}]}
        if payload['type'] == 'send':
            self.sent.append(payload)
            envelope = json.loads(payload['prompt'].split('CURRENT_REQUEST:\n')[1])
            calls = [{'name': 'read_file', 'arguments': {'path': 'src/main.js'}}] if self.mode == 'tool' else []
            answer = {'request_id': envelope['request_id'], 'content': None if calls else '网页回答', 'tool_calls': calls}
            if self.escape_mode:
                answer['content'] = self.repair_padding or None
                answer['tool_calls'] = [{'name': 'read_file', 'arguments': {'path': r'C:\project\file.js'}}]
                if len(self.sent) > 1 and self.bad_repair_schema:
                    answer['tool_calls'][0]['arguments'] = {}
            self.result = json.dumps(answer, ensure_ascii=False)
            if self.escape_mode and (len(self.sent) == 1 or self.escape_mode == 'always'):
                self.result = self.result.replace(r'\\project', r'\project')
            return {'job_id': 'job-1'}
        if self.failure:
            return {'status': 'error', 'error': self.failure}
        if self.waiting:
            return {'status': 'running'}
        if self.mode == 'bad':
            return {'status': 'completed', 'result': 'please execute some code without JSON'}
        return {'status': 'completed', 'result': self.result}


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.web = FakeWeb()
        self.app = endpoint.create_app('local-test-key', 'bound-page', self.web, poll_interval=.001, heartbeat=.002)
        self.life = self.app.router.lifespan_context(self.app)
        await self.life.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://local', headers=HEADERS)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.life.__aexit__(None, None, None)

    async def post(self, **changes):
        return await self.client.post('/v1/chat/completions', json={**BODY, **changes})

    async def test_authentication_and_browser_origin(self):
        for headers, status in [({'Authorization': ''}, 401), ({'Origin': 'https://example.com'}, 403)]:
            r = await self.client.get('/v1/models', headers=headers)
            self.assertEqual(r.status_code, status)
        self.assertFalse(self.web.sent)

    async def test_models_and_unsupported_responses(self):
        self.assertEqual((await self.client.get('/v1/models')).json()['data'][0]['id'], 'web-ai')
        r = await self.client.post('/v1/responses', json={})
        self.assertEqual(r.status_code, 404)
        self.assertIn('error', r.json())

    async def test_protocol_requests_fenced_literal_extraction(self):
        r = await self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.web.sent[0]['response_format'], 'json_code_block')
        self.assertIn('code fence is mandatory', self.web.sent[0]['prompt'])

    async def test_text_and_exact_retry_cached(self):
        r = await self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['choices'][0]['message']['content'], '网页回答')
        again = await self.post()
        self.assertEqual(r.json(), again.json())
        self.assertEqual(len(self.web.sent), 1)

    async def test_tool_call_and_tool_result_followup(self):
        self.web.mode = 'tool'
        r = await self.post(tools=[TOOL], tool_choice='required')
        self.assertEqual(r.status_code, 200)
        choice = r.json()['choices'][0]
        self.assertEqual(choice['finish_reason'], 'tool_calls')
        call = choice['message']['tool_calls'][0]
        self.assertEqual(json.loads(call['function']['arguments']), {'path': 'src/main.js'})
        self.web.mode = 'text'
        messages = BODY['messages'] + [choice['message'], {'role': 'tool', 'tool_call_id': call['id'], 'content': 'actual file content'}]
        r = await self.post(messages=messages, tools=[TOOL])
        self.assertEqual(r.status_code, 200)
        forwarded = json.loads(self.web.sent[-1]['prompt'].split('CURRENT_REQUEST:\n')[1])
        self.assertEqual(forwarded['messages'][-1]['tool_call_id'], call['id'])
        self.assertEqual(forwarded['messages'][-1]['content'], 'actual file content')

    async def test_stream_text_and_cached_nonstream_identity(self):
        r = await self.post(stream=True)
        self.assertIn('text/event-stream', r.headers['content-type'])
        lines = [line[6:] for line in r.text.splitlines() if line.startswith('data: ')]
        self.assertEqual(lines[-1], '[DONE]')
        first = json.loads(lines[0])
        self.assertEqual(first['choices'][0]['delta']['content'], '网页回答')
        r2 = await self.post()
        self.assertEqual(first['id'], r2.json()['id'])
        self.assertEqual(len(self.web.sent), 1)

    async def test_stream_tool_call_shape(self):
        self.web.mode = 'tool'
        r = await self.post(stream=True, tools=[TOOL])
        events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith('data: {')]
        call = events[0]['choices'][0]['delta']['tool_calls'][0]
        self.assertEqual(call['index'], 0)
        self.assertEqual(call['function']['name'], 'read_file')
        self.assertEqual(events[-1]['choices'][0]['finish_reason'], 'tool_calls')

    async def test_bad_answer_is_error_and_not_resent(self):
        self.web.mode = 'bad'
        r = await self.post()
        self.assertEqual(r.status_code, 502)
        self.assertIn('error', r.json())
        self.assertEqual((await self.post()).status_code, 502)
        self.assertEqual(len(self.web.sent), 1)

    async def test_stream_error_never_emits_success(self):
        self.web.failure = 'Login expired'
        r = await self.post(stream=True)
        self.assertIn('"error":', r.text)
        self.assertNotIn('"finish_reason":"stop"', r.text)
        self.assertNotIn('"tool_calls":', r.text)

    async def test_busy_rejected_but_same_request_shared(self):
        self.web.waiting = True
        first = asyncio.create_task(self.post())
        try:
            async with asyncio.timeout(2):
                while not self.web.sent:
                    await asyncio.sleep(.001)
            other = await self.post(messages=[{'role': 'user', 'content': 'different request'}])
            self.assertEqual(other.status_code, 409)
            retry = asyncio.create_task(self.post())
            await asyncio.sleep(.01)
            self.assertEqual(len(self.web.sent), 1)
            self.web.waiting = False
            a, b = await asyncio.gather(first, retry)
            self.assertEqual(a.json(), b.json())
        finally:
            self.web.waiting = False
            await first

    async def test_invalid_inputs_never_reach_webpage(self):
        cases = [
            {'model': 'unknown'}, {'messages': []}, {'stream': 'yes'}, {'tools': None},
            {'parallel_tool_calls': True}, {'tool_choice': 'required'}, {'n': 2},
            {'tool_choice': {'type': 'function', 'function': []}},
            {'tool_choice': {'type': 'function', 'function': {'name': []}}},
            {'stop': ['stop']}, {'response_format': {'type': 'json_object'}},
            {'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'secret'}}]}]},
            {'tools': [{'type': 'function', 'function': {'name': 'remote', 'parameters': {'$ref': 'https://example.com/schema'}}}]},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual((await self.post(**case)).status_code, 400)
        self.assertFalse(self.web.sent)

    async def test_context_and_body_limits_no_truncation(self):
        r = await self.post(messages=[{'role': 'user', 'content': 'x' * 60000}])
        self.assertEqual(r.status_code, 413)
        r = await self.client.post('/v1/chat/completions', content=b'x' * 1000001)
        self.assertEqual(r.status_code, 413)
        self.assertFalse(self.web.sent)

    async def test_provider_budget_accepts_large_cursor_context(self):
        self.web.provider = 'deepseek'
        content = 'x' * 90000
        r = await self.post(messages=[{'role': 'system', 'content': content}] + BODY['messages'])
        self.assertEqual(r.status_code, 200)
        forwarded = json.loads(self.web.sent[0]['prompt'].split('CURRENT_REQUEST:\n')[1])
        self.assertEqual(forwarded['messages'][0]['content'], content)

    async def test_provider_oversize_reports_role_sizes_without_contents(self):
        self.web.provider = 'deepseek'
        r = await self.post(messages=[{'role': 'system', 'content': 'SECRET-MARKER' + 'x' * 161000}])
        self.assertEqual(r.status_code, 413)
        self.assertIn('limit=160000', r.json()['error']['message'])
        self.assertIn('Breakdown', r.json()['error']['message'])
        self.assertNotIn('SECRET-MARKER', r.text)
        self.assertFalse(self.web.sent)

    async def test_invalid_escape_one_shot_repair_and_retry_cache(self):
        self.web.escape_mode = 'once'
        r = await self.post(tools=[TOOL])
        self.assertEqual(r.status_code, 200, r.text)
        call = r.json()['choices'][0]['message']['tool_calls'][0]
        self.assertEqual(json.loads(call['function']['arguments'])['path'], r'C:\project\file.js')
        self.assertEqual(len(self.web.sent), 2)
        self.assertIn('FORMAT REPAIR ONLY', self.web.sent[1]['prompt'])
        self.assertIn('previous_output', self.web.sent[1]['prompt'])
        again = await self.post(tools=[TOOL])
        self.assertEqual(again.json(), r.json())
        self.assertEqual(len(self.web.sent), 2)

    async def test_invalid_escape_repair_stops_after_one_attempt(self):
        self.web.escape_mode = 'always'
        r = await self.post(tools=[TOOL], stream=True)
        self.assertIn('Single format-repair attempt failed', r.text)
        self.assertIn('web_output_json', r.text)
        self.assertNotIn('"finish_reason":"tool_calls"', r.text)
        self.assertEqual(len(self.web.sent), 2)

    async def test_format_repair_still_requires_valid_tool_arguments(self):
        self.web.escape_mode = 'once'
        self.web.bad_repair_schema = True
        r = await self.post(tools=[TOOL])
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()['error']['code'], 'web_arguments_schema')
        self.assertEqual(len(self.web.sent), 2)

    async def test_oversized_repair_is_not_sent(self):
        self.web.escape_mode = 'once'
        self.web.repair_padding = 'x' * 60000
        r = await self.post(tools=[TOOL])
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()['error']['code'], 'web_repair_budget')
        self.assertEqual(len(self.web.sent), 1)

    async def test_invalid_json_and_duplicate_keys(self):
        for text in ('{', '{"model":"web-ai","model":"another"}', '{"x":NaN}'):
            r = await self.client.post('/v1/chat/completions', content=text)
            self.assertEqual(r.status_code, 400)

    async def test_missing_binding_does_not_pick_another_tab(self):
        app = endpoint.create_app('local-test-key', 'missing-page', self.web)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local', headers=HEADERS) as client:
            r = await client.post('/v1/chat/completions', json=BODY)
        self.assertEqual(r.status_code, 409)
        self.assertFalse(self.web.sent)


class AnswerValidationTests(unittest.TestCase):
    def test_invalid_tool_outputs_fail_closed(self):
        body = {**BODY, 'tools': [TOOL]}
        catalog = endpoint.validate_request(body)
        valid = {'request_id': 'r', 'content': None, 'tool_calls': [{'name': 'read_file', 'arguments': {'path': 'x'}}]}
        cases = [
            {**valid, 'request_id': 'old'},
            {**valid, 'tool_calls': [{'name': 'shell', 'arguments': {'command': 'bad'}}]},
            {**valid, 'tool_calls': [{'name': 'read_file', 'arguments': {'path': 123}}]},
            {**valid, 'tool_calls': [{'name': 'read_file', 'arguments': {'path': 'x', 'extra': True}}]},
            {**valid, 'tool_calls': valid['tool_calls'] * 2},
            {**valid, 'content': 42},
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(endpoint.AdapterError):
                    endpoint.parse_answer(json.dumps(case), 'r', body, catalog)
        with self.assertRaises(endpoint.AdapterError):
            endpoint.parse_answer(json.dumps(valid), 'r', {**body, 'tool_choice': 'none'}, catalog)
        wrapped = endpoint.parse_answer('```json\n' + json.dumps(valid) + '\n```', 'r', body, catalog)
        self.assertEqual(wrapped['choices'][0]['finish_reason'], 'tool_calls')


class InputBudgetTests(unittest.TestCase):
    def test_utf16_and_line_limits(self):
        from input_limits import size_error, utf16_units
        self.assertEqual(utf16_units('a😀'), 3)
        self.assertIsNone(size_error('😀' * 80000, {'provider': 'deepseek'}))
        self.assertIsNotNone(size_error('😀' * 80001, {'provider': 'deepseek'}))
        self.assertIsNone(size_error('x' * 118000, {'provider': 'arena'}))
        self.assertIsNotNone(size_error('x' * 118001, {'provider': 'arena'}))
        self.assertIsNone(size_error('x' * 120000, {'provider': 'chatgpt'}))
        self.assertIsNotNone(size_error('x' * 120001, {'provider': 'chatgpt'}))
        self.assertIsNotNone(size_error('\n' * 600, {'provider': 'chatgpt'}))
        self.assertIsNotNone(size_error('x' * 60001, {}))


class EditOutputDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tool = {'type': 'function', 'function': {'name': 'edit_file', 'parameters': {
            'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}},
            'required': ['path', 'content'], 'additionalProperties': False}}}
        self.body = {**BODY, 'tools': [self.tool]}
        self.catalog = endpoint.validate_request(self.body)
        self.code = 'const s = "quoted";\nconst p = "C:\\tmp";\n// 中文 😀\n'

    def parse(self, calls, **overrides):
        value = {'request_id': 'r', 'content': None, 'tool_calls': calls, **overrides}
        return endpoint.parse_answer(json.dumps(value), 'r', self.body, self.catalog)

    def test_edit_payload_preserved_in_both_function_formats(self):
        args = {'path': 'demo.js', 'content': self.code}
        for call in [
            {'name': 'edit_file', 'arguments': args},
            {'name': 'edit_file', 'arguments': json.dumps(args)},
            {'id': 'ignored-web-id', 'type': 'function', 'function': {'name': 'edit_file', 'arguments': json.dumps(args)}}
        ]:
            result = self.parse([call])
            actual = json.loads(result['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
            self.assertEqual(actual, args)

    def test_schema_error_reports_missing_key_not_code(self):
        with self.assertRaises(endpoint.AdapterError) as caught:
            self.parse([{'name': 'edit_file', 'arguments': {'content': 'PRIVATE-CODE'}}])
        self.assertEqual(caught.exception.code, 'web_arguments_schema')
        self.assertIn('path', str(caught.exception))
        self.assertNotIn('PRIVATE-CODE', str(caught.exception))
        self.assertEqual(endpoint.error_body(caught.exception)['error']['code'], 'web_arguments_schema')

    def test_error_stages_are_distinct_and_fail_closed(self):
        cases = [
            ('web_request_identity', [], {'request_id': 'old', 'content': 'answer'}),
            ('web_unknown_tool', [{'name': 'made_up_edit', 'arguments': {}}], {}),
            ('web_arguments_json', [{'name': 'edit_file', 'arguments': '{invalid'}], {}),
            ('web_arguments_schema', [{'name': 'edit_file', 'arguments': {'path': 123, 'content': 'secret'}}], {}),
            ('web_call_count', [{}, {}], {}),
        ]
        for code, calls, kwargs in cases:
            with self.subTest(code=code):
                with self.assertRaises(endpoint.AdapterError) as caught:
                    self.parse(calls, **kwargs)
                self.assertEqual(caught.exception.code, code)

    def test_prose_partial_and_duplicate_json_remain_rejected(self):
        valid = json.dumps({'request_id':'r','content':'ok','tool_calls':[]})
        for text in ['Here is the edit: '+valid, valid+valid, valid[:-1],
                     '{"request_id":"r","request_id":"r","content":"ok","tool_calls":[]}']:
            with self.assertRaises(endpoint.AdapterError) as caught:
                endpoint.parse_answer(text, 'r', self.body, self.catalog)
            self.assertEqual(caught.exception.code, 'web_output_json')

    def test_unescaped_edit_newline_reports_position_without_payload(self):
        text = '{"request_id":"r","content":"PRIVATE\nCODE","tool_calls":[]}'
        with self.assertRaises(endpoint.AdapterError) as caught:
            endpoint.parse_answer(text, 'r', self.body, self.catalog)
        self.assertIn('line 1, column', str(caught.exception))
        self.assertNotIn('PRIVATE', str(caught.exception))
