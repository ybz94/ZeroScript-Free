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
BUILD_ID = 'b7'  # printed in the banner: proves which build is actually running

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


class _TimestampedStream:
    """Wraps the log file so every line is prefixed with HH:MM:SS - the exe
    log reads like a timeline, which makes remote diagnosis (user sends the
    log file) actually possible."""

    def __init__(self, raw):
        self._raw = raw

    def write(self, s):
        if not s:
            return 0
        import datetime
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        out = ''.join(f'[{ts}] {line}' if line.strip() else line
                      for line in s.splitlines(keepends=True))
        return self._raw.write(out)

    def flush(self):
        self._raw.flush()

    def isatty(self):
        return False

    def fileno(self):
        return self._raw.fileno()


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
        stream = _TimestampedStream(open(path, 'a', encoding='utf-8', buffering=1))
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
        self.external_stop = None  # threading.Event set from main thread (windowed)
        self._tasks = []
        self._servers = []
        self.note = None
        self.log_path = None
        self._byok_cache = None

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
            if self.external_stop is not None:
                self.external_stop.set()
            return JSONResponse({'ok': True})

        async def launch_browser(request):
            # Open a dedicated browser (private profile + extension pre-loaded)
            # pointed at the chosen AI site - no manual extension install.
            data = await request.json()
            site = str(data.get('site') or 'deepseek')
            site_url = {pid: url for pid, _name, url in SITES}.get(site)
            if not site_url:
                return JSONResponse({'ok': False, 'error': f'未知站点: {site}'}, status_code=400)
            profile_dir = str(Path(_stable_dir()) / 'cursor-web-profile')
            label, _proc = _launch_dedicated_browser(str(self.ext_dir), site_url, profile_dir)
            if not label:
                return JSONResponse({'ok': False,
                                     'error': '未找到 Edge/Chrome，无法打开专用浏览器'}, status_code=500)
            print(f'[center] 专用浏览器已打开（{label}）→ {site_url}', flush=True)
            return JSONResponse({'ok': True, 'label': label, 'url': site_url})

        async def byok(request):
            data = await request.json()
            action = str(data.get('action') or 'status')
            url = f'http://127.0.0.1:{self.endpoint_port}/v1'
            if action == 'write':
                try:
                    from byok_setup import merge_webai_adapter
                    changed, detail = merge_webai_adapter(url, self.endpoint_key)
                except Exception as exc:
                    return JSONResponse({'ok': False, 'error': f'写入失败: {exc}'}, status_code=500)
                self._byok_cache = None
                print(f'[byok] {detail}', flush=True)
                return JSONResponse({'ok': True, 'changed': changed, 'detail': detail})
            if action == 'launch':
                try:
                    from byok_setup import find_byok_exe
                    exe = find_byok_exe()
                except Exception as exc:
                    return JSONResponse({'ok': False, 'error': f'查找失败: {exc}'}, status_code=500)
                if not exe:
                    return JSONResponse({'ok': False,
                                         'error': '未找到 cursor-byok.exe — 把它放到本程序同目录后重试'},
                                        status_code=404)
                try:
                    subprocess.Popen([str(exe)])
                except Exception as exc:
                    return JSONResponse({'ok': False, 'error': f'启动失败: {exc}'}, status_code=500)
                self._byok_cache = None
                print(f'[byok] 已启动 {exe}', flush=True)
                return JSONResponse({'ok': True, 'exe': str(exe)})
            self._byok_cache = None
            return JSONResponse(self.byok_state())

        app = Starlette(routes=[Route('/', index), Route('/api/status', status),
                                Route('/api/rebind', rebind, methods=['POST']),
                                Route('/api/stop', stop, methods=['POST']),
                                Route('/api/launch-browser', launch_browser, methods=['POST']),
                                Route('/api/byok', byok, methods=['POST'])])
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
        import logging
        # A clean exit on Windows is otherwise a wall of scary-but-harmless
        # tracebacks: uvicorn logs CancelledError from the lifespan tasks,
        # the proactor logs _attach AssertionErrors, and the poller's two
        # in-flight bridge connections (list + jobs) get cut mid-handshake.
        # Our own markers below are enough; keep the log readable.
        for name in ('uvicorn.error', 'uvicorn.access', 'websockets'):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        for s in self._servers:
            s.should_exit = True
        await asyncio.sleep(0.3)  # let the servers drain gracefully
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        print('\n  已停止：Bridge、端点与控制界面均已关闭。', flush=True)

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

    def byok_state(self, ttl=10.0):
        """cursor-byok 状态（10 秒缓存，避免每 2.5s 的轮询都跑 tasklist）。"""
        now = time.time()
        if self._byok_cache and now - self._byok_cache[0] < ttl:
            return self._byok_cache[1]
        try:
            from byok_setup import byok_status
            st = byok_status(f'http://127.0.0.1:{self.endpoint_port}/v1',
                             self.endpoint_key)
        except Exception as exc:
            st = {'error': str(exc)}
        self._byok_cache = (now, st)
        return st

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
            'byok': self.byok_state(),
        }


def _self_check_ui(ui_url):
    """Real HTTP GET of the control page. A TCP-connect probe (like
    _wait_port) only proves something LISTENS; this proves the in-process
    server actually SERVES the page - the definitive answer to a blank
    window (server-side) in the log."""
    import urllib.request
    try:
        with urllib.request.urlopen(ui_url, timeout=10) as resp:
            body = resp.read().decode('utf-8', 'replace')
        title_ok = 'Cursor Web Assistant' in body
        return (resp.status == 200 and title_ok), \
            f'HTTP {resp.status}, {len(body)} 字节, 页面标题 {"✓" if title_ok else "✗"}'
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'


def _loop_exception_handler(loop, context):
    """Quiet the benign Windows/asyncio callback errors (mostly at shutdown):
    cancelled lifespans, connection resets, proactor _attach races. Anything
    else is still printed to the log."""
    exc = context.get('exception')
    if isinstance(exc, (asyncio.CancelledError, ConnectionResetError, BrokenPipeError)):
        return
    if isinstance(exc, (AssertionError, OSError)) and \
            'roactor' in repr(context.get('handle') or ''):
        return
    print(f'[loop] {context.get("message")}: {exc!r}', flush=True)


def _find_system_browsers():
    """[(exe, label)] system browsers that can open a chrome-less app window
    (--app mode: no tabs, no address bar - looks like a desktop app window)."""
    import shutil
    from pathlib import Path as _P
    candidates = []
    for name in ('msedge', 'chrome'):
        p = shutil.which(name)
        if p:
            candidates.append((p, name))
    for p in (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Google\Chrome\Application\chrome.exe',
              r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'):
        if os.path.exists(p):
            candidates.append((p, _P(p).name))
    return candidates


def _open_system_app_window(ui_url):
    """Open the control center in a system-browser app window. Returns
    (label, Popen|None); (None, None) if no browser found."""
    import subprocess
    for exe, label in _find_system_browsers():
        try:
            proc = subprocess.Popen([exe, f'--app={ui_url}'])
            return label, proc
        except Exception:
            continue
    import webbrowser
    try:
        webbrowser.open(ui_url)
        return 'default browser (normal tab)', None
    except Exception:
        return None, None


def _dedicated_browser_command(browser_exe, profile_dir, ext_dir, url):
    """argv to open a DEDICATED browser instance: private profile + our
    extension pre-loaded (no chrome://extensions, no manual install)."""
    return [
        browser_exe,
        f'--user-data-dir={profile_dir}',
        f'--load-extension={ext_dir}',
        f'--disable-extensions-except={ext_dir}',
        url,
    ]


def _launch_dedicated_browser(ext_dir, url, profile_dir):
    """Launch the system browser as a dedicated instance with the extension
    pre-loaded, pointed at `url`. Returns (label, Popen) or (None, None)."""
    import subprocess
    for exe, label in _find_system_browsers():
        try:
            proc = subprocess.Popen(_dedicated_browser_command(exe, profile_dir, ext_dir, url))
            return label, proc
        except Exception:
            continue
    return None, None


def _wait_port(port, tries=100, delay=0.15):
    """True if 127.0.0.1:port accepts a TCP connection within ~tries*delay s."""
    import socket
    for _ in range(tries):
        s = socket.socket()
        s.settimeout(delay)
        try:
            s.connect(('127.0.0.1', port))
            s.close()
            return True
        except OSError:
            time.sleep(delay)
    return False


def run_with_window(center, ui_url, display='auto'):
    """Windowed mode.

    CRITICAL: webview.start() blocks the MAIN thread in the native WinForms
    message pump. The asyncio loop (bridge + endpoint + UI servers) must
    therefore run on a SEPARATE thread - if it shares the main thread, the
    whole pipeline freezes the moment the window opens and the page never
    loads (blank white window).

    display:
      auto   - try the native WebView2 window; if the page never renders
               (WebView2 broken/missing), automatically open the same UI in
               a system-browser app window (chrome-less, looks like a desktop
               app) so the user ALWAYS gets a windowed UI
      native - native window only
      browser- system-browser app window only (the most reliable path)
    """
    import threading

    stop_evt = threading.Event()
    center.external_stop = stop_evt

    def loop_thread():
        async def run():
            asyncio.get_running_loop().set_exception_handler(_loop_exception_handler)
            try:
                await center.start()
                print(f'\n  \u25b6 \u5c31\u7eea\uff1a\u7aef\u70b9 http://127.0.0.1:{center.endpoint_port}/v1 '
                      f'(model: {MODEL})\u3000\u63a7\u5236\u9762\u6771: {ui_url}', flush=True)
                await asyncio.get_running_loop().run_in_executor(None, stop_evt.wait)
            finally:
                await center.shutdown()
        try:
            asyncio.run(run())
        except Exception:
            import traceback
            traceback.print_exc()  # sys.stderr -> cursor_web.log when windowed/frozen

    t = threading.Thread(target=loop_thread, name='services', daemon=True)
    t.start()

    if not _wait_port(center.ui_port):
        print('  \u542f\u52a8\u5931\u8d25\uff1a\u670d\u52a1\u672a\u5c31\u7eea\uff08\u7aef\u53e3\u88ab\u5360\u7528\u6216\u5176\u4ed6\u9519\u8bef\uff09\u2014 \u8be6\u89c1 cursor_web.log',
              flush=True)
        stop_evt.set()
        t.join(timeout=10)
        return 1

    # Definitive blank-window diagnostic: prove the in-process server actually
    # SERVES the page (not just accepts TCP).
    ok, detail = _self_check_ui(ui_url)
    verdict = '\u6b63\u5e38' if ok else '\u5f02\u5e38'
    print(f'  \u81ea\u68c0\uff1a\u63a7\u5236\u9762\u677f\u9875\u9762 {verdict}\uff08{detail}\uff09', flush=True)
    if not ok:
        print(f'  \u8b66\u544a\uff1a\u9875\u9762\u81ea\u68c0\u5931\u8d25\u2014\u53ef\u5728\u6d4f\u89c8\u5668\u6253\u5f00 {ui_url} \u9a8c\u8bc1\u670d\u52a1\u5c42', flush=True)

    if display == 'browser':
        return _run_browser_display(ui_url, stop_evt, t)

    import webview
    try:
        print(f'  \u7a97\u53e3\u5f15\u64ce: pywebview {webview.__version__} (edgechromium/WebView2)', flush=True)
    except Exception:
        pass
    win = webview.create_window('Cursor Web Assistant', ui_url,
                                width=1024, height=800, min_size=(860, 620))

    if display == 'auto':
        # Watchdog: pywebview fires window.events.loaded only once the page has
        # rendered AND its JS bridge ran - it NEVER fires if the WebView2
        # engine itself is broken. So: no 'loaded' within 15s => the native
        # window is blind => open the same UI in a system app window instead,
        # and close the broken one.
        def load_watchdog():
            try:
                if win.events.loaded.wait(15):
                    return
            except Exception:
                return
            label, _proc = _open_system_app_window(ui_url)
            if label:
                print(f'  [\u5151\u5e95] \u5185\u7f6e\u7a97\u53e3 15 \u79d2\u672a\u6e32\u67d3\u9875\u9762\uff08WebView2 \u5f15\u64ce\u53ef\u80fd\u7f3a\u5931\uff09\u2014'
                      f'\u5df2\u7528\u7cfb\u7edf\u6d4f\u89c8\u5668\u5e94\u7528\u7a97\u53e3\u6253\u5f00\u63a7\u5236\u4e2d\u5fc3\uff08{label}\uff09\uff0c\u8bf7\u76f4\u63a5\u4f7f\u7528\u8be5\u7a97\u53e3\u3002',
                      flush=True)
                try:
                    win.destroy()
                except Exception:
                    pass
            else:
                print('  [\u5151\u5e95\u5931\u8d25] \u672a\u627e\u5230 Edge/Chrome\uff0c\u65e0\u6cd5\u6253\u5f00\u7cfb\u7edf\u5e94\u7528\u7a97\u53e3\u2014\u8bf7\u53d1\u6211 cursor_web.log\u3002',
                      flush=True)

        threading.Thread(target=load_watchdog, name='watchdog', daemon=True).start()

    webview.start()  # returns when the window is closed
    print('  \u7a97\u53e3\u5df2\u5173\u95ed\uff0c\u6b63\u5728\u505c\u6b62\u670d\u52a1\u2026', flush=True)
    stop_evt.set()
    t.join(timeout=10)
    return 0


def _run_browser_display(ui_url, stop_evt, services_thread):
    """Most reliable display path: all services run inside the exe; the control
    center shows in a system-browser APP window (chrome-less, looks like a
    desktop app). Exit via the \u505c\u6b62\u7a0b\u5e8f button in the UI."""
    label, proc = _open_system_app_window(ui_url)
    if not label:
        print('  \u542f\u52a8\u5931\u8d25\uff1a\u672a\u627e\u5230 Edge/Chrome\uff0c\u65e0\u6cd5\u6253\u5f00\u7cfb\u7edf\u5e94\u7528\u7a97\u53e3\u3002', flush=True)
        stop_evt.set()
        services_thread.join(timeout=10)
        return 1
    print(f'  \u4f7f\u7528\u7cfb\u7edf\u5e94\u7528\u7a97\u53e3\uff08{label}\uff09\u663e\u793a\u63a7\u5236\u4e2d\u5fc3\u3002', flush=True)
    print('  \u9000\u51fa\u65b9\u5f0f\uff1a\u754c\u9762\u91cc\u70b9"\u505c\u6b62\u7a0b\u5e8f"\uff08\u4f1a\u540c\u65f6\u5173\u95ed\u8be5\u7a97\u53e3\uff09\u3002', flush=True)
    try:
        stop_evt.wait()
    finally:
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--no-window', action='store_true', help='headless (tests/CI)')
    ap.add_argument('--display', choices=['auto', 'native', 'browser'], default='auto',
                    help='window: auto = native with system-app fallback (default); '
                         'native = WebView2 only; browser = system app window only')
    ap.add_argument('--bridge-port', type=int, default=BRIDGE_PORT)
    ap.add_argument('--endpoint-port', type=int, default=ENDPOINT_PORT)
    ap.add_argument('--ui-port', type=int, default=UI_PORT)
    args = ap.parse_args(argv)

    log_path = _ensure_streams()
    print('=' * 56, flush=True)
    print('  Cursor Web Assistant - \u684c\u9762\u63a7\u5236\u4e2d\u5fc3', flush=True)
    print('=' * 56, flush=True)
    print(f'  Python: {sys.version.split()[0]}  ({sys.executable})', flush=True)
    print(f'  \u7248\u672c: {VERSION}  (\u6784\u5efa {BUILD_ID})', flush=True)
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

    # 自动把 web-ai 模型写进 cursor-byok 的配置（幂等、带备份）：
    # 用户不用再在 cursor-byok 里手填地址/密钥。
    try:
        from byok_setup import merge_webai_adapter
        _changed, _detail = merge_webai_adapter(f'http://127.0.0.1:{args.endpoint_port}/v1',
                                                center.endpoint_key)
        print(f'  [cursor-byok] \u6a21\u578b\u914d\u7f6e: {_detail}', flush=True)
    except Exception as exc:
        print(f'  [cursor-byok] \u6a21\u578b\u914d\u7f6e\u5199\u5165\u5931\u8d25: {exc}', flush=True)

    try:
        if args.no_window:
            async def run():
                await center.start()
                print(f'  \u65e0\u7a97\u53e3\u6a21\u5f0f - \u63a7\u5236\u9762\u677f\u4e5f\u53ef\u6253\u5f00: {ui_url}', flush=True)
                await center.stop_event.wait()
                await center.shutdown()
            return asyncio.run(run())
        return run_with_window(center, ui_url, display=args.display)
    except KeyboardInterrupt:
        print('\nStopping\u2026', flush=True)
        return 0


if __name__ == '__main__':
    sys.exit(main())
