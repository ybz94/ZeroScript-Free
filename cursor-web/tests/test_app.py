"""Desktop control-center (app.py) integration test.

Runs the WHOLE pipeline in one process (bridge + endpoint + UI server) in a
subprocess with isolated ports/env, a fake content script that answers
dispatches, and real HTTP through the endpoint. Proves the "one program"
behaviour: status API, rebind, and a full chat completion.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CURSOR_WEB = Path(__file__).resolve().parent.parent

SCRIPT = r'''
import asyncio, json, os, re, sys
sys.path.insert(0, r"CWD")
os.environ["CURSOR_WEB_PORT"] = "17714"
os.environ["CURSOR_WEB_TOKEN_FILE"] = r"TMP/bridge-token"
os.environ["CURSOR_WEB_ENDPOINT_TOKEN_FILE"] = r"TMP/endpoint-token"
import httpx, websockets

SESSION_ID = "sess-app-test"

async def fake_extension():
    token = open(os.environ["CURSOR_WEB_TOKEN_FILE"]).read().strip()
    await asyncio.sleep(0.3)  # let the in-process bridge bind
    async with websockets.connect("ws://127.0.0.1:17714") as ws:
        await ws.send(json.dumps({"token": token, "role": "extension"}))
        await ws.recv()
        while True:
            sess = [{"id": SESSION_ID, "key": "/c/1", "provider": "deepseek",
                     "title": "Test", "url": "https://chat.deepseek.com/c/1",
                     "visible": True, "busy": False, "transportVersion": "0.4.23",
                     "providerVersion": "0.4.23", "inSync": True, "inputMaxChars": 160000}]
            await ws.send(json.dumps({"type": "sessions", "sessions": sess}))
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 1.0))
            except asyncio.TimeoutError:
                continue
            if msg.get("type") != "dispatch":
                continue
            m = re.search(r'"request_id":"([0-9a-fA-F]{6,})"', msg.get("prompt", ""))
            rid = m.group(1) if m else "abc12345"
            text = ('```json\n{"request_id":"%s","content":"hi-from-app","tool_calls":[]}\n```' % rid)
            await ws.send(json.dumps({"type": "result", "job_id": msg["job_id"],
                                      "session_id": msg["session_id"], "text": text,
                                      "error": None, "diagnostics": {}}))

async def main():
    task = asyncio.create_task(fake_extension())
    import app as appmod
    center = appmod.Center("ext-dir", bridge_port=17714, endpoint_port=17715, ui_port=17716)
    await center.start()
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:17716") as ui:
            assert "Cursor Web Assistant" in (await ui.get("/")).text
            state = None
            for _ in range(30):
                state = (await ui.get("/api/status")).json()
                if state["sessions"]:
                    break
                await asyncio.sleep(0.5)
            assert state["sessions"][0]["id"] == SESSION_ID, "session not registered"
            r = await ui.post("/api/rebind", json={"session_id": SESSION_ID})
            assert r.status_code == 200 and r.json()["ok"], r.text
            assert (await ui.get("/api/status")).json()["endpoint"]["bound_ok"]
            key = (await ui.get("/api/status")).json()["endpoint"]["key"]
            async with httpx.AsyncClient(base_url="http://127.0.0.1:17715/v1",
                                         headers={"Authorization": "Bearer " + key},
                                         timeout=30) as ep:
                resp = await ep.post("/chat/completions", json={
                    "model": "web-ai", "messages": [{"role": "user", "content": "hi"}]})
                assert resp.status_code == 200, resp.text
                content = resp.json()["choices"][0]["message"]["content"]
                assert "hi-from-app" in content, content
            for _ in range(20):  # the state poller refreshes every 3s
                state = (await ui.get("/api/status")).json()
                if any(j["status"] == "completed" for j in state["jobs"]):
                    break
                await asyncio.sleep(0.5)
            assert any(j["status"] == "completed" for j in state["jobs"]), state["jobs"]
        print("APP-TEST-OK", flush=True)
    finally:
        task.cancel()
        await center.shutdown()

asyncio.run(main())
'''


class DesktopAppTests(unittest.TestCase):
    def test_one_process_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = SCRIPT.replace("CWD", CURSOR_WEB.as_posix()).replace(
                "TMP", tmp.replace(os.sep, "/"))
            r = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True, timeout=120,
                               cwd=str(CURSOR_WEB))
            self.assertIn("APP-TEST-OK", r.stdout,
                          f"stdout:\n{r.stdout[-2000:]}\nstderr:\n{r.stderr[-2000:]}")

    def test_windowed_exe_no_console(self):
        """Regression: frozen --windowed exe has sys.stdout/stderr == None,
        which crashed uvicorn's logging setup (sys.stdout.isatty())."""
        with tempfile.TemporaryDirectory() as tmp:
            logf = str(Path(tmp) / "cursor_web.log")
            script = (
                "import sys; sys.stdout = None; sys.stderr = None\n"
                "import app\n"
                "p = app._ensure_streams()\n"
                "assert p is not None, 'no log file'\n"
                "import uvicorn\n"
                "from starlette.applications import Starlette\n"
                "uvicorn.Config(Starlette(), host='127.0.0.1', port=17799).configure_logging()\n"
                "print('STREAMS-OK', p)\n"
            )
            r = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True, timeout=60,
                               cwd=str(CURSOR_WEB),
                               env={**os.environ, "CURSOR_WEB_LOG_FILE": logf})
            self.assertEqual(r.returncode, 0,
                             f"script failed (streams were redirected, so the "
                             f"log file has details):\n{Path(logf).read_text(errors='replace') if Path(logf).exists() else ''}")
            self.assertIn("STREAMS-OK", Path(logf).read_text(encoding="utf-8"))

    def test_window_mode_serves_while_main_thread_blocked(self):
        """Regression: webview.start() blocks the main thread in the native
        message pump. If the asyncio loop shared that thread, the in-process
        UI server would freeze and the window would load a BLANK WHITE page.
        Here a fake webview.start() 'blocks' the main thread while the page
        must still be served (it would hang under the old single-thread code)."""
        with tempfile.TemporaryDirectory() as tmp:
            script = (
                "import json, os, sys, time, types, urllib.request\n"
                f"sys.path.insert(0, r'{CURSOR_WEB}')\n"
                f"os.environ['CURSOR_WEB_TOKEN_FILE'] = r'{tmp}/t1'\n"
                f"os.environ['CURSOR_WEB_ENDPOINT_TOKEN_FILE'] = r'{tmp}/t2'\n"
                "os.environ['CURSOR_WEB_PORT'] = '17724'\n"
                "import app\n"
                "fake = types.ModuleType('webview')\n"
                "fake.create_window = lambda *a, **k: None\n"
                "def fake_start():\n"
                "    time.sleep(0.6)  # main thread now 'in the message pump'\n"
                "    html = urllib.request.urlopen('http://127.0.0.1:17726/', timeout=10).read().decode()\n"
                "    assert 'Cursor Web Assistant' in html, 'page not served while main thread blocked'\n"
                "    st = json.loads(urllib.request.urlopen('http://127.0.0.1:17726/api/status', timeout=10).read())\n"
                "    assert st['app']['version'], st\n"
                "    time.sleep(0.4)\n"
                "fake.start = fake_start\n"
                "sys.modules['webview'] = fake\n"
                "c = app.Center('ext', bridge_port=17724, endpoint_port=17725, ui_port=17726)\n"
                "rc = app.run_with_window(c, 'http://127.0.0.1:17726')\n"
                "assert rc == 0, rc\n"
                "print('WINDOW-MODE-OK')\n"
            )
            r = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True, timeout=90,
                               cwd=str(CURSOR_WEB))
            self.assertIn("WINDOW-MODE-OK", r.stdout,
                          f"stdout:\n{r.stdout[-2000:]}\nstderr:\n{r.stderr[-2000:]}")

    def _run_fake_webview_scenario(self, loaded, expect_fallback, port_base):
        """Run run_with_window with a fake webview whose 'loaded' event
        behaves per `loaded` ('never' | 'fast'); assert the system-app
        fallback did / did not kick in."""
        with tempfile.TemporaryDirectory() as tmp:
            bp, ep, up = port_base, port_base + 1, port_base + 2
            script = (
                "import os, sys, time, types\n"
                f"sys.path.insert(0, r'{CURSOR_WEB}')\n"
                f"os.environ['CURSOR_WEB_TOKEN_FILE'] = r'{tmp}/t1'\n"
                f"os.environ['CURSOR_WEB_ENDPOINT_TOKEN_FILE'] = r'{tmp}/t2'\n"
                f"os.environ['CURSOR_WEB_PORT'] = '{bp}'\n"
                "import app\n"
                "calls = {'fallback': [], 'destroy': []}\n"
                "app._open_system_app_window = lambda url: (calls['fallback'].append(url) or ('fake-edge', None))\n"
                "class FakeWin:\n"
                "    def __init__(self):\n"
                "        self.events = types.SimpleNamespace(loaded=self)\n"
                "        self.destroyed = False\n"
                f"    def wait(self, timeout=None):\n"
                f"        time.sleep(0.2 if '{loaded}' == 'never' else 0.01)\n"
                f"        return '{loaded}' != 'never'\n"
                "    def destroy(self):\n"
                "        self.destroyed = True\n"
                "fake_win = FakeWin()\n"
                "fake = types.ModuleType('webview')\n"
                "fake.__version__ = '9.9-test'\n"
                "fake.create_window = lambda *a, **k: fake_win\n"
                "def fake_start():\n"
                "    time.sleep(1.0)\n"
                "    if " + str(expect_fallback) + ":\n"
                "        assert fake_win.destroyed, 'fallback did not destroy broken window'\n"
                "        assert calls['fallback'], 'fallback app window not opened'\n"
                "    else:\n"
                "        assert not fake_win.destroyed, 'fallback must not run when page loaded'\n"
                "        assert not calls['fallback'], 'fallback must not run when page loaded'\n"
                "fake.start = fake_start\n"
                "sys.modules['webview'] = fake\n"
                f"c = app.Center('ext', bridge_port={bp}, endpoint_port={ep}, ui_port={up})\n"
                f"rc = app.run_with_window(c, 'http://127.0.0.1:{up}')\n"
                "assert rc == 0, rc\n"
                "print('FAKE-WINDOW-OK')\n"
            )
            r = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True, timeout=90,
                               cwd=str(CURSOR_WEB))
            self.assertIn("FAKE-WINDOW-OK", r.stdout,
                          f"stdout:\n{r.stdout[-2000:]}\nstderr:\n{r.stderr[-2000:]}")

    def test_fallback_when_page_never_loads(self):
        """Native window blind (WebView2 broken => 'loaded' never fires) =>
        the system-browser app window must open and the broken window close."""
        self._run_fake_webview_scenario('never', True, 17734)

    def test_no_fallback_when_page_loads(self):
        """Native window renders ('loaded' fires) => no fallback, no destroy."""
        self._run_fake_webview_scenario('fast', False, 17744)

    def test_dedicated_browser_command(self):
        """The dedicated-browser argv must carry a private profile + the
        pre-loaded extension + the target site (no manual install)."""
        sys.path.insert(0, str(CURSOR_WEB))
        try:
            import app as appmod
            edge = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
            prof, ext = r'E:\app\cursor-web-profile', r'E:\app\extension'
            argv = appmod._dedicated_browser_command(edge, prof, ext, 'https://chat.deepseek.com')
            self.assertEqual(argv[0], edge)
            self.assertIn('--user-data-dir=' + prof, argv)
            self.assertIn('--load-extension=' + ext, argv)
            self.assertIn('--disable-extensions-except=' + ext, argv)
            self.assertIn('https://chat.deepseek.com', argv)
        finally:
            sys.path.pop(0)

    def test_launch_browser_endpoint(self):
        """/api/launch-browser opens the dedicated browser for the chosen site
        (launcher mocked; unknown site => 400)."""
        with tempfile.TemporaryDirectory() as tmp:
            script = (
                "import asyncio, json, os, sys\n"
                f"sys.path.insert(0, r'{CURSOR_WEB}')\n"
                f"os.environ['CURSOR_WEB_TOKEN_FILE'] = r'{tmp}/t1'\n"
                f"os.environ['CURSOR_WEB_ENDPOINT_TOKEN_FILE'] = r'{tmp}/t2'\n"
                "os.environ['CURSOR_WEB_PORT'] = '17754'\n"
                "import app as appmod\n"
                "calls = []\n"
                "appmod._launch_dedicated_browser = lambda ext_dir, url, profile_dir: "
                "(calls.append((ext_dir, url, profile_dir)) or ('fake-edge', None))\n"
                "async def main():\n"
                "    import httpx\n"
                "    center = appmod.Center('ext-dir', bridge_port=17754, endpoint_port=17755, ui_port=17756)\n"
                "    await center.start()\n"
                "    try:\n"
                "        async with httpx.AsyncClient(base_url='http://127.0.0.1:17756') as ui:\n"
                "            r = await ui.post('/api/launch-browser', json={'site':'deepseek'})\n"
                "            assert r.status_code==200 and r.json()['ok'], r.text\n"
                "            assert calls and calls[0][1]=='https://chat.deepseek.com', calls\n"
                "            r2 = await ui.post('/api/launch-browser', json={'site':'nope'})\n"
                "            assert r2.status_code==400, r2.text\n"
                "    finally:\n"
                "        await center.shutdown()\n"
                "    print('LAUNCH-BROWSER-OK')\n"
                "asyncio.run(main())\n"
            )
            r = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True, timeout=90,
                               cwd=str(CURSOR_WEB))
            self.assertIn("LAUNCH-BROWSER-OK", r.stdout,
                          f"stdout:\n{r.stdout[-2000:]}\nstderr:\n{r.stderr[-2000:]}")


class ByokSetupTests(unittest.TestCase):
    """Pure unit tests for byok_setup.merge_webai_adapter (no Windows needed)."""

    def _mod(self):
        sys.path.insert(0, str(CURSOR_WEB))
        import byok_setup as b
        return b

    def test_merge_creates_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._mod()
            p = Path(tmp) / 'config.yaml'
            changed, detail = b.merge_webai_adapter('http://127.0.0.1:17615/v1', 'KEY1', p)
            self.assertTrue(changed, detail)
            import yaml
            cfg = yaml.safe_load(p.read_text(encoding='utf-8'))
            a = {x['modelID']: x for x in cfg['modelAdapters']}['web-ai']
            self.assertEqual(a['baseURL'], 'http://127.0.0.1:17615/v1')
            self.assertEqual(a['apiKey'], 'KEY1')
            self.assertEqual(a['type'], 'openai')
            self.assertEqual(a['openAIEndpoint'], '/v1/chat/completions')
            self.assertEqual(cfg['proxyListenAddr'], '127.0.0.1:18080')
            self.assertEqual(cfg['backendListenAddr'], '127.0.0.1:18090')
            self.assertEqual(cfg['routing'], {'mode': 'local'})
            self.assertFalse(p.with_suffix('.yaml.bak').exists())  # no backup for a new file

    def test_merge_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._mod()
            p = Path(tmp) / 'config.yaml'
            url, key = 'http://127.0.0.1:17615/v1', 'KEY1'
            b.merge_webai_adapter(url, key, p)
            first = p.read_bytes()
            changed, detail = b.merge_webai_adapter(url, key, p)
            self.assertFalse(changed, detail)
            self.assertEqual(p.read_bytes(), first)  # no rewrite when up to date

    def test_merge_updates_stale_key_preserves_extras(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._mod()
            import yaml
            p = Path(tmp) / 'config.yaml'
            p.write_text(yaml.safe_dump({
                'backendListenAddr': '127.0.0.1:18999',
                'modelAdapters': [{
                    'displayName': '网页 AI (Cursor Web Assistant)', 'type': 'openai',
                    'baseURL': 'http://old:9999/v1', 'apiKey': 'OLDKEY',
                    'tooltipData': 'x', 'modelID': 'web-ai', 'reasoningEffort': 'high',
                    'openAIEndpoint': '/v1/chat/completions', 'contextWindowTokens': 200000,
                    'maxCompletionTokens': 32000, 'customHeadersEnabled': True,
                }],
            }, allow_unicode=True), encoding='utf-8')
            changed, _ = b.merge_webai_adapter('http://127.0.0.1:17615/v1', 'NEWKEY', p)
            self.assertTrue(changed)
            cfg = yaml.safe_load(p.read_text(encoding='utf-8'))
            a = [x for x in cfg['modelAdapters'] if x['modelID'] == 'web-ai'][0]
            self.assertEqual(a['apiKey'], 'NEWKEY')
            self.assertEqual(a['baseURL'], 'http://127.0.0.1:17615/v1')
            self.assertEqual(a['reasoningEffort'], 'low')          # our value wins
            self.assertTrue(a.get('customHeadersEnabled'))          # user extras kept
            self.assertEqual(cfg['backendListenAddr'], '127.0.0.1:18999')  # top-level kept
            self.assertTrue(p.with_suffix('.yaml.bak').exists())    # backup written

    def test_merge_preserves_other_adapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._mod()
            import yaml
            p = Path(tmp) / 'config.yaml'
            other = {'displayName': 'deepseek', 'type': 'openai', 'baseURL': 'http://ds/v1',
                     'apiKey': 'DS', 'tooltipData': 't', 'modelID': 'deepseek',
                     'reasoningEffort': 'medium', 'openAIEndpoint': '/v1/chat/completions',
                     'contextWindowTokens': 64000, 'maxCompletionTokens': 8000}
            p.write_text(yaml.safe_dump({'modelAdapters': [other]}), encoding='utf-8')
            changed, _ = b.merge_webai_adapter('http://127.0.0.1:17615/v1', 'K', p)
            self.assertTrue(changed)
            cfg = yaml.safe_load(p.read_text(encoding='utf-8'))
            self.assertEqual([a['modelID'] for a in cfg['modelAdapters']],
                             ['deepseek', 'web-ai'])

    def test_merge_refuses_unparseable(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._mod()
            p = Path(tmp) / 'config.yaml'
            p.write_text('{{{not yaml: [', encoding='utf-8')
            before = p.read_bytes()
            changed, detail = b.merge_webai_adapter('http://127.0.0.1:17615/v1', 'K', p)
            self.assertFalse(changed)
            self.assertIn('无法解析', detail)
            self.assertEqual(p.read_bytes(), before)  # untouched


class ByokEndpointTests(unittest.TestCase):
    def test_byok_write_status_launch(self):
        """/api/byok: write auto-fills cursor-byok's model config (no manual
        address/key), status reflects it, launch starts the located exe."""
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'cursor-byok.exe'
            exe.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
            exe.chmod(0o755)
            script = (
                "import asyncio, os, sys, yaml\n"
                f"sys.path.insert(0, r'{CURSOR_WEB}')\n"
                f"os.environ['CURSOR_WEB_TOKEN_FILE'] = r'{tmp}/t1'\n"
                f"os.environ['CURSOR_WEB_ENDPOINT_TOKEN_FILE'] = r'{tmp}/t2'\n"
                f"os.environ['CURSOR_BYOK_CONFIG'] = r'{tmp}/byok-config.yaml'\n"
                f"os.environ['CURSOR_BYOK_EXE'] = r'{tmp}/cursor-byok.exe'\n"
                "os.environ['CURSOR_WEB_PORT'] = '17758'\n"
                "import app as appmod, httpx\n"
                "async def main():\n"
                "    center = appmod.Center('ext-dir', bridge_port=17758, endpoint_port=17759, ui_port=17760)\n"
                "    await center.start()\n"
                "    try:\n"
                "        async with httpx.AsyncClient(base_url='http://127.0.0.1:17760') as ui:\n"
                "            st = (await ui.get('/api/status')).json()\n"
                "            assert st['byok']['config_exists'] is False, st['byok']\n"
                "            r = await ui.post('/api/byok', json={'action':'write'})\n"
                "            assert r.status_code == 200 and r.json()['ok'] and r.json()['changed'], r.text\n"
                "            key = (await ui.get('/api/status')).json()['endpoint']['key']\n"
                f"            cfg = yaml.safe_load(open(r'{tmp}/byok-config.yaml', encoding='utf-8'))\n"
                "            a = [x for x in cfg['modelAdapters'] if x['modelID'] == 'web-ai'][0]\n"
                "            assert a['apiKey'] == key, a\n"
                "            assert a['baseURL'] == 'http://127.0.0.1:17759/v1', a\n"
                "            r2 = await ui.post('/api/byok', json={'action':'write'})\n"
                "            assert r2.json()['ok'] and r2.json()['changed'] is False, r2.text\n"
                "            st2 = (await ui.get('/api/status')).json()\n"
                "            assert st2['byok']['config_exists'] is True, st2['byok']\n"
                "            assert st2['byok']['adapter_ok'] is True, st2['byok']\n"
                "            r3 = await ui.post('/api/byok', json={'action':'launch'})\n"
                "            assert r3.status_code == 200 and r3.json()['ok'], r3.text\n"
                "            st3 = (await ui.get('/api/status')).json()\n"
                "            assert st3['byok']['byok_exe'].endswith('cursor-byok.exe'), st3['byok']\n"
                "    finally:\n"
                "        await center.shutdown()\n"
                "    print('BYOK-ENDPOINT-OK')\n"
                "asyncio.run(main())\n"
            )
            r = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True, timeout=90,
                               cwd=str(CURSOR_WEB))
            self.assertIn("BYOK-ENDPOINT-OK", r.stdout,
                          f"stdout:\n{r.stdout[-2000:]}\nstderr:\n{r.stderr[-2000:]}")


if __name__ == "__main__":
    unittest.main()
