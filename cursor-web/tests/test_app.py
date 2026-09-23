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


if __name__ == "__main__":
    unittest.main()
