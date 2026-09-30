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
                     "visible": True, "busy": False, "transportVersion": "0.4.28",
                     "providerVersion": "0.4.28", "inSync": True, "inputMaxChars": 160000}]
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

    def test_kill_stale_dedicated(self):
        """Before opening the dedicated page, kill orphaned instances left
        over from previous builds: they still hold our profile dir and the
        OLD extension, and Chromium reuses that live instance (ignoring the
        fresh --load-extension) - the live 2026-09-29 409 storm. Matching is
        by our profile dir in the command line only (the user's main browser
        is never touched); best-effort - never blocks the launch."""
        import unittest.mock as mock
        sys.path.insert(0, str(CURSOR_WEB))
        try:
            import app as appmod
            prof = r'E:\app\cursor-web-profile'
            # non-Windows: no-op, subprocess never touched
            with mock.patch.object(appmod.os, 'name', 'posix'), \
                 mock.patch.object(appmod.subprocess, 'run') as run:
                self.assertEqual(appmod._kill_stale_dedicated(prof), 0)
                run.assert_not_called()
            # Windows: powershell scan + hidden console; count from stdout
            class R:
                stdout = '  2\n'
            with mock.patch.object(appmod.os, 'name', 'nt'), \
                 mock.patch.object(appmod.subprocess, 'run', return_value=R()) as run:
                self.assertEqual(appmod._kill_stale_dedicated(prof), 2)
            cmd = run.call_args.args[0]
            kw = run.call_args.kwargs
            self.assertEqual(cmd[0], 'powershell')
            joined = ' '.join(cmd)
            self.assertIn(prof, joined)          # match on OUR profile dir
            self.assertIn('taskkill', joined)    # kill the process trees
            self.assertIn('powershell', joined)  # …but never the scanner itself
            self.assertIn(kw.get('creationflags', 0), (0, 0x08000000))  # CREATE_NO_WINDOW
            # failures (timeout/parse) are best-effort: 0, never raises
            with mock.patch.object(appmod.os, 'name', 'nt'), \
                 mock.patch.object(appmod.subprocess, 'run', side_effect=OSError('boom')):
                self.assertEqual(appmod._kill_stale_dedicated(prof), 0)
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

    def test_cancel_task_endpoint(self):
        """/api/cancel aborts the in-flight task on the dedicated page:
        center -> bridge -> extension (cancel) -> normal result path frees
        the session. The fake page 'thinks' until it is cancelled."""
        with tempfile.TemporaryDirectory() as tmp:
            script = (
                "import asyncio, json, os, sys\n"
                f"sys.path.insert(0, r'{CURSOR_WEB}')\n"
                f"os.environ['CURSOR_WEB_TOKEN_FILE'] = r'{tmp}/t1'\n"
                f"os.environ['CURSOR_WEB_ENDPOINT_TOKEN_FILE'] = r'{tmp}/t2'\n"
                "os.environ['CURSOR_WEB_PORT'] = '17764'\n"
                "import app as appmod, httpx, websockets\n"
                "async def fake_extension():\n"
                "    token = open(os.environ['CURSOR_WEB_TOKEN_FILE']).read().strip()\n"
                "    await asyncio.sleep(0.3)\n"
                "    async with websockets.connect('ws://127.0.0.1:17764') as ws:\n"
                "        await ws.send(json.dumps({'role': 'extension', 'token': token}))\n"
                "        await ws.recv()\n"
                "        while True:\n"
                "            await ws.send(json.dumps({'type': 'sessions', 'sessions': [{'id': 's1', 'key': '/c/1', 'provider': 'deepseek', 'title': 'T', 'url': 'https://x/c/1', 'visible': True, 'busy': False, 'inputMaxChars': 100000}]}))\n"
                "            msg = json.loads(await asyncio.wait_for(ws.recv(), 1.0))\n"
                "            if msg.get('type') == 'dispatch':\n"
                "                continue  # the page is 'thinking' - task stays running\n"
                "            if msg.get('type') == 'cancel':\n"
                "                await ws.send(json.dumps({'type': 'result', 'job_id': msg['job_id'], 'error': '任务被取消'}))\n"
                "async def main():\n"
                "    center = appmod.Center('ext-dir', bridge_port=17764, endpoint_port=17765, ui_port=17766)\n"
                "    await center.start()\n"
                "    try:\n"
                "        ext = asyncio.create_task(fake_extension())\n"
                "        await asyncio.sleep(0.6)\n"
                "        async with websockets.connect('ws://127.0.0.1:17764') as cur:\n"
                "            await cur.send(json.dumps({'role': 'cursor', 'token': open(os.environ['CURSOR_WEB_TOKEN_FILE']).read().strip()}))\n"
                "            await cur.recv()\n"
                "            await cur.send(json.dumps({'type': 'send', 'session_id': 's1', 'prompt': 'hello'}))\n"
                "            job = json.loads(await cur.recv())\n"
                "            assert job.get('job_id'), job\n"
                "            async with httpx.AsyncClient(base_url='http://127.0.0.1:17766') as ui:\n"
                "                r = await ui.post('/api/cancel', json={'job_id': job['job_id']})\n"
                "                assert r.status_code == 200 and r.json()['ok'], r.text\n"
                "                js = []\n"
                "                for _ in range(30):\n"
                "                    st = (await ui.get('/api/status')).json()\n"
                "                    js = [j for j in st['jobs'] if j['job_id'] == job['job_id']]\n"
                "                    if js and js[0]['status'] == 'error':\n"
                "                        break\n"
                "                    await asyncio.sleep(0.3)\n"
                "                assert js and js[0]['status'] == 'error', st['jobs']\n"
                "                assert '取消' in str(js[0].get('error', '')), js\n"
                "                r2 = await ui.post('/api/cancel', json={})\n"
                "                assert r2.status_code == 400, r2.text\n"
                "        ext.cancel()\n"
                "        print('CANCEL-OK', flush=True)\n"
                "    finally:\n"
                "        await center.shutdown()\n"
                "asyncio.run(main())\n"
            )
            r = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True, timeout=90,
                               cwd=str(CURSOR_WEB))
            self.assertIn("CANCEL-OK", r.stdout,
                          f"stdout:\n{r.stdout[-2000:]}\nstderr:\n{r.stderr[-2000:]}")

    def test_failed_task_is_not_replayed_on_identical_resend(self):
        """A task that FAILED must not be replayed from the dedup cache:
        the identical resend has to start a FRESH task (the page's own
        busy/generating guards still prevent double-sending). Before the
        fix, the cache answered every identical retry with the same error
        forever, and - combined with a still-running bridge job - the user
        could neither succeed nor get a clear failure."""
        script = (
            "import asyncio, json, re, sys\n"
            f"sys.path.insert(0, r'{CURSOR_WEB}')\n"
            "import httpx\n"
            "from model_endpoint import create_app\n"
            "jobs = {}\n"
            "order = 0\n"
            "async def fake_rpc(payload):\n"
            "    global order\n"
            "    t = payload['type']\n"
            "    if t == 'list':\n"
            "        return {'sessions': [{'id': 's1', 'key': '/c/1', 'provider': 'deepseek', 'title': 'T', 'url': 'https://x/c/1', 'visible': True, 'busy': False, 'inputMaxChars': 160000}]}\n"
            "    if t == 'send':\n"
            "        order += 1\n"
            "        jobs['job%d' % order] = {'order': order, 'prompt': payload['prompt']}\n"
            "        return {'job_id': 'job%d' % order, 'status': 'running'}\n"
            "    if t == 'get':\n"
            "        j = jobs[payload['job_id']]\n"
            "        if j['order'] == 1:\n"
            "            return {'job_id': payload['job_id'], 'status': 'error', 'error': 'boom (first attempt failed)'}\n"
            "        rid = re.search(r'\"request_id\":\"([0-9a-f]{6,})\"', j['prompt']).group(1)\n"
            "        text = '```json\\n{\"request_id\":\"%s\",\"content\":\"second-attempt-ok\",\"tool_calls\":[]}\\n```' % rid\n"
            "        return {'job_id': payload['job_id'], 'status': 'completed', 'result': text, 'diagnostics': {}}\n"
            "    if t == 'cancel':\n"
            "        return {'job_id': payload['job_id'], 'status': 'running', 'cancel_requested': True}\n"
            "    raise ValueError(t)\n"
            "app = create_app('k', 's1', rpc=fake_rpc, poll_interval=0.01)\n"
            "async def main():\n"
            "    body = {'model': 'web-ai', 'stream': False, 'messages': [{'role': 'user', 'content': 'hello dedup'}], 'response_format': {'type': 'text'}}\n"
            "    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://t') as cli:\n"
            "        h = {'Authorization': 'Bearer k'}\n"
            "        r1 = await cli.post('/v1/chat/completions', headers=h, json=body)\n"
            "        assert r1.status_code == 502, r1.text\n"
            "        assert 'boom' in r1.text, r1.text\n"
            "        r2 = await cli.post('/v1/chat/completions', headers=h, json=body)  # identical resend\n"
            "        assert r2.status_code == 200, r2.text  # fresh task, NOT a replay of the failure\n"
            "        assert r2.json()['choices'][0]['message']['content'] == 'second-attempt-ok', r2.text\n"
            "    print('DEDUP-OK', flush=True)\n"
            "asyncio.run(main())\n"
        )
        r = subprocess.run([sys.executable, "-c", script],
                           capture_output=True, text=True, timeout=90,
                           cwd=str(CURSOR_WEB))
        self.assertIn("DEDUP-OK", r.stdout,
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


class DownloadFileTests(unittest.TestCase):
    """cloudflared 下载：字节级进度、完成落盘、HTTP 错误路径。"""

    def _serve(self, handler_cls, directory):
        import http.server
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler_cls)
        import threading
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return srv

    def test_download_with_progress_and_error(self):
        import functools
        import http.server
        import app as appmod
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            payload = bytes(range(256)) * 4096  # 1MB
            (tmp / 'cloudflared.exe').write_bytes(payload)

            class H(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *a, **kw):
                    super().__init__(*a, directory=str(tmp), **kw)
                def log_message(self, *a):
                    pass

            srv = self._serve(H, tmp)
            port = srv.server_address[1]
            try:
                got, seen = [], None
                def cb(g, t):
                    got.append((g, t))
                dst = tmp / 'out' / 'cloudflared.exe'
                dst.parent.mkdir()
                r_got, r_total = appmod.download_file(f'http://127.0.0.1:{port}/cloudflared.exe', dst, cb)
                self.assertEqual(dst.read_bytes(), payload, 'bytes differ')
                self.assertEqual(r_got, len(payload))
                self.assertEqual(got[-1][0], len(payload), 'progress ended short')
                self.assertEqual(got[-1][1], len(payload), 'content-length surfaced')
                self.assertEqual(got[0], (0, len(payload)), 'first callback is (0, total)')
                # 404 path raises
                with self.assertRaises(Exception):
                    appmod.download_file(f'http://127.0.0.1:{port}/nope.exe', tmp / 'x')
                self.assertFalse((tmp / 'x').exists(), 'partial file must not be renamed on error')
            finally:
                srv.shutdown()
                srv.server_close()


class TunnelUrlTests(unittest.TestCase):
    def test_tunnel_url_from_line(self):
        import app as appmod
        self.assertIsNone(appmod._tunnel_url_from_line('cloudflared 2026.1.0'))
        self.assertIsNone(appmod._tunnel_url_from_line(''))
        v = appmod._tunnel_url_from_line('Request URL: https://abc-def.trycloudflare.com')
        self.assertEqual(v, 'https://abc-def.trycloudflare.com')
        v = appmod._tunnel_url_from_line('INFO your quick tunnel has been created at https://x-y-z-1.trycloudflare.com')
        self.assertEqual(v, 'https://x-y-z-1.trycloudflare.com')
        # non-tunnel URLs are ignored
        self.assertIsNone(appmod._tunnel_url_from_line('https://example.com/mcp'))


class FileMcpInProcessTests(unittest.TestCase):
    """The exe's promise: file MCP runs IN-PROCESS (no separate Python) and
    the cloudflared tunnel is managed from the UI. On Linux the tunnel can
    only be tested up to the cloudflared-binary lookup (Windows-only exe);
    the MCP half is fully exercised over real HTTP + a real MCP client."""

    def test_file_mcp_in_process_and_tunnel_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            stable = tmp / 'stable'
            root = tmp / 'project'
            stable.mkdir()
            (root / 'src').mkdir(parents=True)
            (root / 'src' / 'a.py').write_text('hello-mcp\nworld\n', encoding='utf-8')
            script = (
                "import asyncio, os, sys, json, httpx\n"
                f"sys.path.insert(0, r'{CURSOR_WEB}')\n"
                f"os.environ['CURSOR_WEB_TOKEN_FILE'] = r'{tmp}/bridge-token'\n"
                f"os.environ['CURSOR_WEB_ENDPOINT_TOKEN_FILE'] = r'{tmp}/endpoint-token'\n"
                f"os.environ['CURSOR_WEB_STABLE_DIR'] = r'{stable}'\n"
                "os.environ['CURSOR_WEB_PORT'] = '17762'\n"
                "import app as appmod\n"
                "async def main():\n"
                "    center = appmod.Center('ext-dir', bridge_port=17762, endpoint_port=17763, ui_port=17764)\n"
                "    await center.start()\n"
                "    try:\n"
                "        async with httpx.AsyncClient(base_url='http://127.0.0.1:17764', timeout=15) as ui:\n"
                f"            root = r'{root}'\n"
                "            # not enabled by default\n"
                "            st = (await ui.get('/api/status')).json()\n"
                "            assert st['file_mcp']['enabled'] is False and st['file_mcp']['running'] is False, st['file_mcp']\n"
                "            # enable via the same endpoint the UI button uses\n"
                "            r = await ui.post('/api/file-mcp', json={'root': root, 'enabled': True, 'port': 17765})\n"
                "            assert r.status_code == 200 and r.json()['ok'], r.text\n"
                "            assert r.json()['file_mcp']['running'] is True, r.text\n"
                "            st = (await ui.get('/api/status')).json()\n"
                "            assert st['file_mcp']['running'] is True and st['file_mcp']['root'] == root, st['file_mcp']\n"
                "            token = st['file_mcp']['token']\n"
                "            assert token, 'token missing in status'\n"
                "            mcp_url = 'http://127.0.0.1:17765/mcp'\n"
                "            # persisted to config.json next to the exe\n"
                f"            cfg = json.load(open(r'{stable}/config.json', encoding='utf-8'))\n"
                "            assert cfg['file_mcp_root'] == root and cfg['file_mcp_enabled'] is True, cfg\n"
                "            # real MCP client, Bearer header\n"
                "            from mcp import ClientSession\n"
                "            from mcp.client.streamable_http import streamablehttp_client\n"
                "            async with streamablehttp_client(mcp_url, headers={'Authorization': 'Bearer ' + token},\n"
                "                                             timeout=10, sse_read_timeout=20) as c:\n"
                "                async with ClientSession(c[0], c[1]) as s:\n"
                "                    await s.initialize()\n"
                "                    read = await s.call_tool('read_file', {'path': 'src/a.py'})\n"
                "                    assert 'hello-mcp' in read.content[0].text, read.content\n"
                "            # ... and the ?token= URL the UI shows for pasting\n"
                "            async with streamablehttp_client(mcp_url + '?token=' + token,\n"
                "                                             timeout=10, sse_read_timeout=20) as c:\n"
                "                async with ClientSession(c[0], c[1]) as s:\n"
                "                    await s.initialize()\n"
                "                    assert 'world' in (await s.call_tool('read_file', {'path': 'src/a.py'})).content[0].text\n"
                "            # tunnel state: no cloudflared binary on this box -> managed fields exist\n"
                "            assert 'cloudflared' in st['tunnel'] and st['tunnel']['running'] is False, st['tunnel']\n"
                "            r = await ui.post('/api/tunnel', json={'action': 'start'})\n"
                "            assert r.status_code == 500 and 'cloudflared' in r.json()['error'], r.text\n"
                "            r = await ui.post('/api/pick-dir', json={'path': ''})\n"
                "            assert r.status_code == 500 and '直接输入' in r.json()['error'], r.text\n"
                "            st = (await ui.get('/api/status')).json()\n"
                "            assert st['tunnel']['cloudflared_downloaded'] == 0 and st['tunnel']['cloudflared_error'] is None, st['tunnel']\n"
                "            assert 'releases' in st['tunnel']['cloudflared_page'], st['tunnel']\n"
                "            import pathlib; stable = pathlib.Path(os.environ['CURSOR_WEB_STABLE_DIR'])\n"
                "            fake = stable / 'cloudflared.exe'\n"
                "            fake.write_text('#!/bin/sh\\necho \"Request URL: https://abc-def.trycloudflare.com\"\\nsleep 300\\n')\n"
                "            _os_chmod = __import__('os').chmod\n"
                "            _os_chmod(fake, 0o755)\n"
                "            r = await ui.post('/api/tunnel', json={'action': 'start'})\n"
                "            assert r.status_code == 200 and r.json()['ok'], r.text\n"
                "            st = None\n"
                "            for _ in range(50):\n"
                "                st = (await ui.get('/api/status')).json()\n"
                "                if st['tunnel']['url']:\n"
                "                    break\n"
                "                await asyncio.sleep(0.2)\n"
                "            assert st['tunnel']['url'] == 'https://abc-def.trycloudflare.com', st['tunnel']\n"
                "            assert st['tunnel']['running'] is True, st['tunnel']\n"
                "            assert st['file_mcp']['connect_url'] == 'https://abc-def.trycloudflare.com/mcp?token=' + token, st['file_mcp']\n"
                "            assert os.environ.get('ZW_FILE_MCP_URL') == 'https://abc-def.trycloudflare.com/mcp?token=' + token, 'auto-carry env not published'\n"
                "            r = await ui.post('/api/tunnel', json={'action': 'stop'})\n"
                "            assert r.status_code == 200 and r.json()['ok'], r.text\n"
                "            st = (await ui.get('/api/status')).json()\n"
                "            assert st['tunnel']['running'] is False and st['tunnel']['url'] is None, st['tunnel']\n"
                "            assert st['file_mcp']['connect_url'] is None, st['file_mcp']\n"
                "            assert 'ZW_FILE_MCP_URL' not in os.environ, 'auto-carry env not cleared after tunnel stop'\n"
                "            # disable -> server stops\n"
                "            r = await ui.post('/api/file-mcp', json={'enabled': False})\n"
                "            assert r.status_code == 200 and r.json()['ok'], r.text\n"
                "            st = (await ui.get('/api/status')).json()\n"
                "            assert st['file_mcp']['running'] is False, st['file_mcp']\n"
                "    finally:\n"
                "        await center.shutdown()\n"
                "    print('FILE-MCP-INPROC-OK')\n"
                "asyncio.run(main())\n"
            )
            r = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True, timeout=90,
                               cwd=str(CURSOR_WEB))
            self.assertIn("FILE-MCP-INPROC-OK", r.stdout,
                          f"stdout:\n{r.stdout[-2000:]}\nstderr:\n{r.stderr[-2000:]}")


if __name__ == "__main__":
    unittest.main()
