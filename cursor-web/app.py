"""Cursor Web Assistant - desktop control center (one program = whole pipeline).

A single process runs everything local:
  * the Bridge          (WebSocket broker, port 17614)
  * the model endpoint  (OpenAI-compatible, for cursor-byok, port 17615)
  * the control center  (native window via WebView2, UI server port 17616)

Run from the repo:    python app.py            (or double-click start.bat)
Headless / tests:     python app.py --no-window
Frozen build:         dist/CursorWebAssistant.exe (double-click build_exe.bat once)

Only two things CANNOT be packed into an exe (platform security, by design):
  * the Chrome extension - load it once, guided by the wizard in the window;
  * cursor-byok - your Go adapter; the window shows the config to paste.
"""
import argparse
import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE_PORT = int(os.getenv('CURSOR_WEB_PORT', '17614'))
ENDPOINT_PORT = int(os.getenv('CURSOR_WEB_ENDPOINT_PORT', '17615'))
UI_PORT = int(os.getenv('CURSOR_WEB_UI_PORT', '17616'))
MODEL = 'web-ai'
VERSION = '0.4.23'

SITES = [
    ('deepseek', 'DeepSeek', 'https://chat.deepseek.com'),
    ('glm', 'GLM \u00b7 Z.ai', 'https://chat.z.ai'),
    ('kimi', 'Kimi \u00b7 K3', 'https://www.kimi.ai'),
    ('qwen', 'Qwen \u901a\u4e49', 'https://chat.qwen.ai'),
    ('gemini', 'Gemini', 'https://gemini.google.com'),
    ('meta', 'Meta AI', 'https://www.meta.ai'),
    ('chatgpt', 'ChatGPT', 'https://chatgpt.com'),
    ('arena', 'Arena', 'https://arena.ai'),
]


def _stable_dir():
    """Token files must survive restarts: next to the exe when frozen
    (_MEIPASS is a temp dir that vanishes), else in the cursor-web folder."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return HERE


def _ensure_streams():
    """A windowed (no-console) exe has sys.stdout/stderr == None, which crashes
    uvicorn's log setup (it calls sys.stdout.isatty()). Point both at a log
    file instead - next to the exe, and it doubles as the debug log the user
    can send when something goes wrong. Returns the log path or None."""
    if sys.stdout is not None and sys.stderr is not None:
        return None
    try:
        path = Path(os.getenv('CURSOR_WEB_LOG_FILE')
                    or (Path(_stable_dir()) / 'cursor_web.log'))
        stream = open(path, 'a', encoding='utf-8', buffering=1)
    except Exception:
        stream = open(os.devnull, 'w', encoding='utf-8')
        return None
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    return path


def res_path(name):
    """Resource path, frozen (PyInstaller _MEIPASS) or plain."""
    base = getattr(sys, '_MEIPASS', str(HERE))
    return Path(base) / name


class SessionRef:
    """Mutable session binding: create_app() accepts a callable returning the
    CURRENT id, so the control window can rebind without restarting anything."""

    def __init__(self, current=None):
        self.current = current

    def __call__(self):
        return self.current


def _make_token_file(path):
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32))
    return path


def ensure_extension():
    """Return (extension_dir, note). Frozen: release bundled files next to the
    exe (Chrome needs a stable folder, _MEIPASS is a temp dir). Plain: run
    prepare_extension.py so git pull stays safe."""
    if getattr(sys, 'frozen', False):
        src, dst = res_path('extension'), Path(sys.executable).resolve().parent / 'extension'
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return dst, '扩展文件已释放到程序同目录的 extension\\'
    r = subprocess.run([sys.executable, str(HERE / 'prepare_extension.py')],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return HERE / 'extension', 'prepare_extension.py 失败：' + (r.stderr.strip()[:200] or '未知错误')
    return HERE / 'extension', None


class Center:
    """Owns the three in-process services + the state the UI displays."""

    def __init__(self, ext_dir, bridge_port=BRIDGE_PORT, endpoint_port=ENDPOINT_PORT,
                 ui_port=UI_PORT):
        self.ext_dir = Path(ext_dir)
        self.bridge_port = bridge_port
        self.endpoint_port = endpoint_port
        self.ui_port = ui_port
        stable = _stable_dir()
        self.token_path = Path(os.getenv('CURSOR_WEB_TOKEN_FILE', str(stable / '.bridge-token')))
        self.token = _make_token_file(self.token_path).read_text().strip()
        self.endpoint_key_path = Path(os.getenv('CURSOR_WEB_ENDPOINT_TOKEN_FILE',
                                                str(stable / '.endpoint-token')))
        self.endpoint_key = _make_token_file(self.endpoint_key_path).read_text().strip()
        self.session = SessionRef(None)
        self.sessions = []
        self.jobs = []
        self.started_at = time.time()
        self.stop_event = asyncio.Event()
        self._tasks = []
        self._servers = []
        self.note = None
        self.log_path = None

    # -- lifecycle ----------------------------------------------------------
    async def start(self):
        import bridge as bridge_mod
        import model_endpoint as ep
        import uvicorn
        # Keep every token in ONE stable place (next to the exe when frozen):
        # the bridge module, the in-process endpoint RPC, and the UI all read
        # the same file.
        os.environ['CURSOR_WEB_TOKEN_FILE'] = str(self.token_path)
        bridge_mod.TOKEN_FILE = self.token_path
        self._tasks.append(asyncio.create_task(bridge_mod.main(), name='bridge'))
        # bridge.main() binds BRIDGE_PORT (module-level env) - the launcher
        # sets CURSOR_WEB_PORT before import when a custom port is needed.
        self._tasks.append(asyncio.create_task(self._poll(), name='poll'))
        cfg = uvicorn.Config(ep.create_app(self.endpoint_key, self.session),
                             host='127.0.0.1', port=self.endpoint_port, log_level='warning')
        srv = uvicorn.Server(cfg)
        self._servers.append(srv)
        self._tasks.append(asyncio.create_task(srv.serve(), name='endpoint'))
        from starlette.applications import Starlette
        from starlette.responses import HTMLResponse, JSONResponse
        from starlette.routing import Route

        async def index(request):
            return HTMLResponse(res_path('web') .joinpath('index.html').read_text('utf-8'))

        async def status(request):
            return JSONResponse(self.state())

        async def rebind(request):
            data = await request.json()
            sid = str(data.get('session_id') or '').strip()
            if not any(s.get('id') == sid for s in self.sessions):
                return JSONResponse({'ok': False, 'error': '没有这个会话；刷新后重试'}, status_code=400)
            self.session.current = sid
            print(f'[center] rebound endpoint to session {sid}', flush=True)
            return JSONResponse({'ok': True, 'session_id': sid})

        async def stop(request):
            self.stop_event.set()
            return JSONResponse({'ok': True})

        app = Starlette(routes=[Route('/', index), Route('/api/status', status),
                                Route('/api/rebind', rebind, methods=['POST']),
                                Route('/api/stop', stop, methods=['POST'])])
        cfg2 = uvicorn.Config(app, host='127.0.0.1', port=self.ui_port, log_level='warning')
        srv2 = uvicorn.Server(cfg2)
        self._servers.append(srv2)
        self._tasks.append(asyncio.create_task(srv2.serve(), name='ui'))
        # wait for the UI to come up before the window loads it
        import socket
        for _ in range(50):
            s = socket.socket()
            s.settimeout(0.2)
            try:
                s.connect(('127.0.0.1', self.ui_port))
                s.close()
                break
            except OSError:
                await asyncio.sleep(0.1)

    async def shutdown(self):
        for s in self._servers:
            s.should_exit = True
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- state ----------------------------------------------------------------
    async def _poll(self):
        import model_endpoint as ep
        while True:
            try:
                listing = await ep.bridge_rpc({'type': 'list'})
                self.sessions = listing.get('sessions', []) or []
                jobs = await ep.bridge_rpc({'type': 'jobs'})
                self.jobs = jobs.get('jobs', []) or []
            except Exception:
                pass
            await asyncio.sleep(3)

    def state(self):
        bound = self.session.current
        bound_ok = any(s.get('id') == bound for s in self.sessions)
        return {
            'app': {'version': VERSION, 'python': sys.version.split()[0],
                    'frozen': bool(getattr(sys, 'frozen', False)),
                    'uptime_s': int(time.time() - self.started_at),
                    'log': str(self.log_path) if self.log_path else None},
            'bridge': {'port': self.bridge_port, 'running': True},
            'endpoint': {'port': self.endpoint_port, 'model': MODEL,
                         'url': f'http://127.0.0.1:{self.endpoint_port}/v1',
                         'session_id': bound, 'bound_ok': bound is not None and bound_ok,
                         'key': self.endpoint_key},
            'extension': {'dir': str(self.ext_dir), 'note': self.note},
            'token': self.token,
            'sites': SITES,
            'sessions': self.sessions,
            'jobs': self.jobs,
        }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--no-window', action='store_true', help='headless (tests/CI)')
    ap.add_argument('--bridge-port', type=int, default=BRIDGE_PORT)
    ap.add_argument('--endpoint-port', type=int, default=ENDPOINT_PORT)
    ap.add_argument('--ui-port', type=int, default=UI_PORT)
    args = ap.parse_args(argv)

    log_path = _ensure_streams()
    print('=' * 56, flush=True)
    print('  Cursor Web Assistant - \u684c\u9762\u63a7\u5236\u4e2d\u5fc3', flush=True)
    print('=' * 56, flush=True)
    print(f'  Python: {sys.version.split()[0]}  ({sys.executable})', flush=True)
    if log_path:
        print(f'  \u65e0\u63a7\u5236\u53f0\u73af\u5883\uff0c\u65e5\u5fd7\u5199\u5165: {log_path}', flush=True)

    ext_dir, note = ensure_extension()
    print(f'  \u6269\u5c55\u76ee\u5f55: {ext_dir}' + (f'  ({note})' if note else ''), flush=True)

    # bridge.main() reads CURSOR_WEB_PORT at import time (module constant) -
    # set it for custom ports BEFORE the import happens.
    if args.bridge_port != int(os.getenv('CURSOR_WEB_PORT', '17614')):
        os.environ['CURSOR_WEB_PORT'] = str(args.bridge_port)

    center = Center(ext_dir, bridge_port=args.bridge_port,
                    endpoint_port=args.endpoint_port, ui_port=args.ui_port)
    center.note = note
    center.log_path = log_path
    ui_url = f'http://127.0.0.1:{args.ui_port}'

    async def run():
        await center.start()
        print(f'\n  \u25b6 \u5c31\u7eea\uff1a\u7aef\u70b9 http://127.0.0.1:{args.endpoint_port}/v1 (model: {MODEL})', flush=True)
        if args.no_window:
            print(f'    \u65e0\u7a97\u53e3\u6a21\u5f0f - \u63a7\u5236\u9762\u6771\u4e5f\u53ef\u6253\u5f00: {ui_url}', flush=True)
            await center.stop_event.wait()
        else:
            import webview
            webview.create_window('Cursor Web Assistant', ui_url,
                                  width=1024, height=800, min_size=(860, 620))
            webview.start()  # returns when the window is closed
        return 0

    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        print('\nStopping\u2026', flush=True)
        return 0


if __name__ == '__main__':
    sys.exit(main())
