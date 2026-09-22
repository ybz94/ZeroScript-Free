"""One-click local launcher: Bridge + model endpoint in a single command.

Instead of:  bridge.py  →  model_endpoint.py --sessions  →  copy ID  →
model_endpoint.py --session <ID>,  just run:

    python launcher.py            (or double-click start.bat on Windows)

It (1) checks/installs Python deps, (2) refreshes the extension files,
(3) starts the local Bridge, (4) waits for the dedicated webpage session,
(5) starts the model endpoint bound to it. Ctrl+C stops everything.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE_PORT = int(os.getenv('CURSOR_WEB_PORT', '17614'))
ENDPOINT_PORT_DEFAULT = 17615


def step(n, msg):
    print(f"\n[{n}/4] {msg}", flush=True)


def fatal(msg):
    print(f"\n✗ {msg}", flush=True)
    sys.exit(1)


def check_python_deps():
    import importlib.util
    missing = [m for m in ("websockets", "httpx", "uvicorn")
               if importlib.util.find_spec(m) is None]
    if not missing:
        return
    step(0, f"Installing missing Python dependencies: {', '.join(missing)}")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                        str(HERE / "requirements.txt")])
    if r.returncode != 0:
        fatal("pip install failed - run manually:\n  "
              f"{sys.executable} -m pip install -r requirements.txt")
    print("  ✓ dependencies installed", flush=True)


def prepare_extension():
    step(1, "Refreshing extension files (prepare_extension.py)")
    r = subprocess.run([sys.executable, str(HERE / "prepare_extension.py")],
                       capture_output=True, text=True)
    if r.returncode == 0:
        out = (r.stdout.strip().splitlines() or ["extension files copied"])
        print("  ✓ " + out[-1], flush=True)
    else:
        print("  ! prepare failed (continuing): " + r.stderr.strip()[:200], flush=True)
    print("  → 若刚升级过版本：在 chrome://extensions 重新加载扩展，并刷新专用页。", flush=True)


def bridge_already_running():
    import websockets

    async def probe():
        try:
            async with websockets.connect(f"ws://127.0.0.1:{BRIDGE_PORT}",
                                          open_timeout=2) as ws:
                await ws.send(json.dumps({"token": token(), "role": "cursor"}))
                return (await ws.recv()).strip().startswith("{")
        except Exception:
            return False

    return asyncio.run(probe())


def token():
    return (HERE / ".bridge-token").read_text().strip()


def start_bridge():
    step(2, f"Starting local Bridge (port {BRIDGE_PORT})")
    if bridge_already_running():
        print(f"  ✓ Bridge already running on port {BRIDGE_PORT} (launcher will not stop it)",
              flush=True)
        return None
    proc = subprocess.Popen([sys.executable, str(HERE / "bridge.py")],
                            cwd=HERE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    time.sleep(1.0)
    if proc.poll() is not None:
        out = proc.stdout.read() if proc.stdout else ""
        fatal(f"Bridge failed to start (port {BRIDGE_PORT} in use?):\n{out[:500]}")
    print(f"  ✓ Bridge running (pid {proc.pid})", flush=True)
    return proc


def wait_for_sessions(timeout=90):
    import websockets

    async def query():
        async with websockets.connect(f"ws://127.0.0.1:{BRIDGE_PORT}",
                                      open_timeout=3) as ws:
            await ws.send(json.dumps({"token": token(), "role": "cursor"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "list"}))
            return json.loads(await ws.recv())

    step(3, "Waiting for the dedicated webpage session…")
    print("  （浏览器专用标签页打开聊天网站 → 刷新页面 → 弹窗确认已连接）", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            res = asyncio.run(query())
            sessions = res.get("sessions", [])
            if sessions:
                return sessions
        except Exception:
            pass
        time.sleep(2)
    fatal("90s 内没有会话。请：打开聊天网站专用标签页并刷新；"
          "确认扩展弹窗显示绿色“已连接”；令牌 = cursor-web/.bridge-token 内容。")


def host(u):
    return str(u or "").replace("https://", "").replace("http://", "").split("/")[0] or "?"


def report(s):
    if not s.get("inSync"):
        print(f"  ⚠ 版本不同步（content {s.get('transportVersion')} vs provider "
              f"{s.get('providerVersion') or '缺失'}）：运行 prepare_extension.py → "
              "重载扩展 → 刷新专用页，再绑定。", flush=True)
    if s.get("busy"):
        print("  ⚠ 该会话正在执行任务（busy）——等它结束，或稍后再绑定。", flush=True)


def pick(sessions):
    print(flush=True)
    for i, s in enumerate(sessions, 1):
        mark = "" if len(sessions) == 1 else f"[{i}] "
        print(f"  {mark}{s.get('provider', '?')}  @ {host(s.get('url'))}"
              f"  (id {s.get('id')})", flush=True)
    if len(sessions) == 1:
        s = sessions[0]
        print("  → 使用唯一的会话", flush=True)
        report(s)
        return s
    choice = input("  输入序号选择会话（回车=1）: ").strip()
    try:
        idx = max(1, min(len(sessions), int(choice))) - 1
    except ValueError:
        idx = 0
    s = sessions[idx]
    report(s)
    return s


def start_endpoint(session, port):
    step(4, "Starting model endpoint (Cursor → webpage)")
    proc = subprocess.Popen([sys.executable, str(HERE / "model_endpoint.py"),
                             "--session", session["id"], "--port", str(port)],
                            cwd=HERE)
    time.sleep(1.0)
    if proc.poll() is not None:
        fatal(f"model_endpoint.py exited immediately (port {port} in use? "
              "旧端点没停干净：任务管理器结束 python 再试)")
    print(f"  ✓ Endpoint: http://127.0.0.1:{port}/v1   (model 名: web-ai)", flush=True)
    return proc


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=ENDPOINT_PORT_DEFAULT)
    args = ap.parse_args()

    print("=" * 52, flush=True)
    print("  Cursor Web Assistant — 一键启动 (Bridge + 端点)", flush=True)
    print("=" * 52, flush=True)

    check_python_deps()
    prepare_extension()
    bridge = start_bridge()
    endpoint = None
    try:
        sessions = wait_for_sessions()
        session = pick(sessions)
        endpoint = start_endpoint(session, args.port)
        print(flush=True)
        print(f"  ▶ 就绪：Cursor → http://127.0.0.1:{args.port}/v1 (model: web-ai)", flush=True)
        print("    直接开始用 Cursor 对话；按 Ctrl+C 停止全部。", flush=True)
        print(flush=True)
        endpoint.wait()
    except KeyboardInterrupt:
        print("\nStopping…", flush=True)
    finally:
        for p in (endpoint, bridge):
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        time.sleep(0.5)
        print("Bye.", flush=True)


if __name__ == "__main__":
    main()
