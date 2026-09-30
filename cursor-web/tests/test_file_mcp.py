"""Integration test for file_mcp.py: a real HTTP MCP server (streamable-http)
serving a temp project root, driven by a real MCP client. Proves the
"file externalization" backend: token auth, read-only confinement, and the
file-read tools the webpage model would call."""
import asyncio
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('file_mcp', ROOT / 'file_mcp.py')
file_mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(file_mcp)


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FileMcpTests(unittest.IsolatedAsyncioTestCase):
    async def _start_server(self, tmp, port):
        env = {**os.environ, 'ZW_FILE_MCP_ROOT': str(tmp),
               'ZW_FILE_MCP_PORT': str(port), 'ZW_FILE_MCP_TOKEN': 'test-token-123'}
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / 'file_mcp.py'), '--port', str(port)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):  # wait for uvicorn to bind
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors='replace')
                raise AssertionError(f'server exited early:\n{out}')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.3):
                    break
            except OSError:
                await asyncio.sleep(0.1)
        return proc

    async def _stop(self, proc):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    async def test_auth_read_and_confinement(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'src').mkdir()
            (tmp / 'src' / 'a.py').write_text('line1\nline2\nline3\n', encoding='utf-8')
            port = _free_port()
            proc = await self._start_server(tmp, port)
            try:
                url = f'http://127.0.0.1:{port}/mcp'
                # 1) wrong token is rejected
                try:
                    async with streamablehttp_client(url, headers={'Authorization': 'Bearer wrong'},
                                                     timeout=5, sse_read_timeout=10) as c:
                        async with ClientSession(c[0], c[1]) as s:
                            await s.initialize()
                    self.fail('wrong token was accepted')
                except Exception:
                    pass
                # 2) correct token: list_dir + read_file
                async with streamablehttp_client(url, headers={'Authorization': 'Bearer test-token-123'},
                                                 timeout=10, sse_read_timeout=30) as c:
                    async with ClientSession(c[0], c[1]) as s:
                        await s.initialize()
                        listed = await s.call_tool('list_dir', {'path': 'src'})
                        entries = listed.content[0].text
                        self.assertIn('a.py', entries)
                        read = await s.call_tool('read_file', {'path': 'src/a.py'})
                        body = read.content[0].text
                        self.assertIn('line1', body)
                        self.assertIn('line3', body)
                        self.assertIn('total_lines', body)
                # 3) path confinement: escape attempts fail, no exception leaks
                async with streamablehttp_client(url, headers={'Authorization': 'Bearer test-token-123'},
                                                 timeout=10, sse_read_timeout=30) as c:
                    async with ClientSession(c[0], c[1]) as s:
                        await s.initialize()
                        esc = await s.call_tool('read_file', {'path': '../outside-root.txt'})
                        esc_text = esc.content[0].text
                        self.assertIn('outside the exposed project root', esc_text)
                # 4) ?token= query parameter also authenticates (easy URL to paste
                #    in a webpage's MCP settings)
                async with streamablehttp_client(f'{url}?token=test-token-123',
                                                 timeout=10, sse_read_timeout=30) as c:
                    async with ClientSession(c[0], c[1]) as s:
                        await s.initialize()
                        read = await s.call_tool('read_file', {'path': 'src/a.py'})
                        self.assertIn('line2', read.content[0].text)
            finally:
                await self._stop(proc)

    async def test_tunnel_host_header_accepted(self):
        """Requests routed through the Cloudflare tunnel arrive with the
        random per-run trycloudflare.com Host header (the edge forwards its
        own hostname). FastMCP bound to loopback auto-enables the SDK's
        DNS-rebinding Host allowlist (localhost only), which rejected those
        with `421 Invalid Host header` - the exact error the webpage hit.
        Send the tunnel-style Host over a raw socket (the MCP client libs
        always derive Host from the local URL, so they can't reproduce it)
        and assert the server answers normally; the token gate must still
        apply to foreign hosts."""
        import http.client
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            proc = await self._start_server(Path(tmp), port)
            try:
                body = json.dumps({
                    'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                               'clientInfo': {'name': 'tunnel-repro', 'version': '1.0'}},
                }).encode()

                def raw_post(path, host):
                    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
                    # skip_host=True: we set the (foreign, tunnel-style) Host
                    # ourselves; a duplicate Host is malformed HTTP.
                    conn.putrequest('POST', path, skip_accept_encoding=True, skip_host=True)
                    conn.putheader('Host', host)
                    conn.putheader('Content-Type', 'application/json')
                    conn.putheader('Accept', 'application/json, text/event-stream')
                    conn.putheader('Content-Length', str(len(body)))
                    conn.endheaders()
                    conn.send(body)
                    resp = conn.getresponse()
                    payload = resp.read()
                    conn.close()
                    return resp.status, payload

                # 1) tunnel-style Host + correct token -> initialize succeeds
                status, payload = raw_post('/mcp?token=test-token-123',
                                           'period-segment-helicopter-country.trycloudflare.com')
                self.assertEqual(status, 200, payload[:300])
                self.assertIn(b'cursor-web-files', payload)  # serverInfo in the result

                # 2) token gate still enforced for a foreign Host
                status, payload = raw_post('/mcp?token=wrong',
                                           'period-segment-helicopter-country.trycloudflare.com')
                self.assertEqual(status, 401, payload[:300])

                # 3) loopback Host unaffected
                status, _ = raw_post('/mcp?token=test-token-123', f'127.0.0.1:{port}')
                self.assertEqual(status, 200)
            finally:
                await self._stop(proc)


if __name__ == '__main__':
    unittest.main()
