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


if __name__ == "__main__":
    unittest.main()
