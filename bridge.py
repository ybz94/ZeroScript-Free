# # SPDX-License-Identifier: GPL-3.0-or-later
# bridge.py
# ──────────────────────────────────────────────────────────────────────────
#  ZeroScript Bridge
#  Local WebSocket <-> local MCP server(s). Every stdio MCP server declared
#  in config.json (filesystem, git, GitHub, Blender, ...) works the same way.
#  The browser extension talks to this over ws://127.0.0.1:<PORT>.
#
#  What this bridge exposes (aggregated into one tools/list):
#    - Every MCP server declared in config.json, each spawned as a stdio
#      child and routed by tool name.
#
#  Design goals (robustness first):
#   - Each MCP stdio process is read by ONE dedicated thread; responses are
#     matched by JSON-RPC id (no "read the next line and hope" races).
#   - stderr is drained so a child never blocks on a full pipe.
#   - A dead server is auto-restarted and the failing call retried once.
#   - Tool calls are locked PER SERVER, so a slow server never blocks another.
#   - Every call ALWAYS produces a reply: a result OR a structured error.
#     Nothing ever hangs the agentic loop silently.
# ──────────────────────────────────────────────────────────────────────────
import asyncio
import http.server
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid

try:
    import websockets
except ImportError:
    print("[bridge] Missing dependency. Run:  pip install websockets")
    sys.exit(1)

# Windows consoles often default to a legacy codepage (cp1252): printing
# non-ASCII text then raises UnicodeEncodeError INSIDE the WS handler, which
# kills the connection. Force UTF-8 (best effort). We also keep all console
# output strictly ASCII (no arrows / dots) so nothing garbles on a console that
# stayed on a legacy codepage anyway.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _enable_ansi_colors():
    """On Windows, turn on ANSI escape processing so color codes render instead
    of printing as literal gibberish like "<ESC>[92m". Returns True on success."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(k.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:
        return False


HOST = "127.0.0.1"
# Keep in sync with zeroscript-extension/manifest.json "version" - printed at
# startup so a user's terminal output alone tells us which build they're on.
BRIDGE_VERSION = "2.0.0"
PORT = int(os.environ.get("ZS_BRIDGE_PORT", "17613"))
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")

if _enable_ansi_colors():
    C = {
        "reset": "\033[0m", "dim": "\033[2m", "gr": "\033[92m",
        "yl": "\033[93m", "rd": "\033[91m", "cy": "\033[96m",
    }
else:
    C = {k: "" for k in ("reset", "dim", "gr", "yl", "rd", "cy")}

# Every run appends here (never truncated), so a whole test session - across
# multiple restarts - stays in one file the user can just send us. Each
# process start writes a banner (see main()) so restarts are easy to spot.
LOGS_DIR = os.path.join(HERE, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOGS_DIR, "bridge_debug.log")
try:
    _log_file = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
except Exception:
    _log_file = None


class _Spinner:
    """Terminal-only progress indicator for waits that can run several seconds
    (server launch/handshake) so the console never
    just sits there looking dead - the #1 thing that makes a user assume the
    bridge hung and close the window. Purely cosmetic: writes over its own line
    with \\r, never touches bridge_debug.log, and is skipped entirely when
    stdout isn't a real console (redirected to a file, no ANSI)."""
    FRAMES = "|/-\\"
    # Only ONE spinner may animate at a time: server launches now run in
    # PARALLEL (see MCPManager.start_all), and several spinners fighting over
    # the same console line with \r produced interleaved garbage. Whoever
    # acquires this lock animates; the others silently skip (the log lines
    # around them still tell the story).
    _active = threading.Lock()

    def __init__(self, label):
        self.label = label
        self._stop = threading.Event()
        self._thread = None
        self._owns_lock = False

    def __enter__(self):
        if sys.stdout.isatty() and _Spinner._active.acquire(blocking=False):
            self._owns_lock = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            # Wipe the spinner line so the next log() line doesn't get glued
            # onto trailing spinner characters.
            print("\r" + " " * (len(self.label) + 4) + "\r", end="", flush=True)
        if self._owns_lock:
            _Spinner._active.release()

    def _run(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            print(f"\r{C['dim']}{self.label} {frame}{C['reset']}", end="", flush=True)
            i += 1
            self._stop.wait(0.15)


def _clear_spinner_line():
    """Wipe whatever a live _Spinner (running on its own thread, mid-frame) left
    on the current console line via bare \\r writes, so the next print() below
    doesn't get glued onto its trailing characters - seen live 2026-07-14: an
    a banner fired while '[server] starting... -' was still mid-line and
    the red box rendered smashed onto it instead of starting on a fresh line.
    \\033[K (clear to end of line) doesn't depend on knowing the spinner's label
    length the way Spinner.__exit__'s own wipe does."""
    if sys.stdout.isatty():
        print("\r\033[K", end="", flush=True)


def log(msg, color="dim", terminal=True):
    """terminal=False: written to bridge_debug.log only, not the console. Use
    for noisy/technical detail (raw stderr from child MCP servers, per-call
    traces) that would bury the handful of lines a non-technical user actually
    needs to read. Nothing is ever lost - it all still lands in the file."""
    if terminal:
        _clear_spinner_line()
        ts = time.strftime("%H:%M:%S")
        print(f"{C['dim']}{ts}{C['reset']} {C.get(color,'')}{msg}{C['reset']}", flush=True)
    if _log_file:
        try:
            _log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
            _log_file.flush()
        except Exception:
            pass


def _port_owner(port):
    """(pid, name, path) of the process LISTENING on `port`, or None. Win32 only."""
    if sys.platform != "win32":
        return None
    # BOTH stacks: "-p TCP" alone is IPv4-only, and a squatter listening on
    # [::1]:<port> (IPv6 loopback) was then completely invisible to this probe
    # even while Get-NetTCPConnection showed it plainly (the likely reason the
    # boot-time squatter check stayed silent on a machine where ropilot
    # provably held the port - see the 2026-07-13 live report).
    out = ""
    for proto in ("TCP", "TCPv6"):
        try:
            out += subprocess.run(
                ["netstat", "-ano", "-p", proto],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=8,
            ).stdout
        except Exception:
            pass
    if not out:
        return None
    pid = None
    # v4 lines end the local address in ":<port>", v6 in "]:<port>" - matching
    # on the ":<port> " suffix (with the column gap) covers both shapes.
    needle = f":{port} "
    for line in out.splitlines():
        if "LISTENING" in line and needle in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                pid = parts[-1]
                break
    if not pid:
        return None
    name, path = "?", ""
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
             f"if($p){{$p.Name; $p.Path}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8,
        ).stdout.splitlines()
        ps = [l.strip() for l in ps if l.strip()]
        if ps:
            name = ps[0]
            path = ps[1] if len(ps) > 1 else ""
    except Exception:
        pass
    return (pid, name, path)


def _process_cmdline(pid):
    """Full command line of `pid`, or "" if it can't be read. Win32 only.

    Used to tell OUR OWN kind of process (a python running bridge.py) apart
    from an unrelated app that merely happens to listen on the same port -
    the process NAME is just "python"/"py"/"pythonw", far too generic to kill
    on. The command line is what proves it is a leftover bridge."""
    if sys.platform != "win32":
        return ""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" "
             f"-ErrorAction SilentlyContinue).CommandLine"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8,
        ).stdout
    except Exception:
        return ""
    return (out or "").strip()


def _reclaim_bridge_port():
    """Free OUR OWN listen port (17613) from a leftover bridge before we bind.

    The common failure (reported live, WinError 10048 on bind): the user
    relaunches start.bat while an earlier bridge.py is still running - window
    closed with the X instead of Ctrl+C, a previous crash that left a detached
    python, or a double double-click. The old process still holds the port, so
    websockets.serve() dies on bind with a cryptic (localised) OSError and the
    whole bridge exits code 1.

    We reuse _port_owner (already generic over the port) and only ever kill a
    process we can PROVE is another bridge.py - never a same-name innocent
    (some unrelated python listening on 17613): the guard is the command line
    containing "bridge.py", plus an explicit self-exclusion by PID. Anything
    else (a non-python app, or a python whose cmdline we can't read) is left
    alone and surfaced to the user by the caller's friendly bind-error path.
    Returns True if a leftover bridge was killed."""
    owner = _port_owner(PORT)
    if not owner:
        return False
    pid, name, path = owner
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_i == os.getpid():
        return False  # never kill ourselves (defensive; we haven't bound yet)
    # Must look like a python interpreter AND be running bridge.py. Killing on
    # the port alone would murder whatever legitimately owns 17613.
    if "python" not in (name or "").lower() and "py" != (name or "").lower():
        return False
    cmdline = _process_cmdline(pid_i)
    if "bridge.py" not in cmdline.lower():
        log(f"port {PORT} is held by pid {pid_i} ('{name}') but it does not look "
            f"like a ZeroScript bridge - leaving it alone.", "yl")
        return False
    log(f"port {PORT} is held by a leftover ZeroScript bridge (pid {pid_i}) from a "
        "previous session - killing it so this one can start.", "yl")
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid_i)],
                       capture_output=True, text=True, timeout=8)
    except Exception as e:
        log(f"could not kill the leftover bridge (pid {pid_i}): {e}", "rd")
        return False
    log(f"killed the leftover bridge (pid {pid_i}); the port is free now.", "cy")
    return True


# ── config.json read / write (for extension-driven add/remove) ──────────────
def _read_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                cfg.setdefault("mcpServers", {})
                return cfg
        except Exception as e:
            log(f"config.json unreadable ({e}) - starting from a fresh one", "yl")
    return {"mcpServers": {}}


def _write_config(cfg):
    """Atomic write so a crash mid-write never leaves a truncated config.json."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def config_add_server(server_id, command, args=None, env=None):
    """Add/replace a server in config.json. Returns (ok, error)."""
    sid = (server_id or "").strip()
    if not sid:
        return False, "server id is required"
    if not (command or "").strip():
        return False, "a command is required"
    cfg = _read_config()
    spec = {"command": command.strip(), "args": list(args or [])}
    if env:
        spec["env"] = dict(env)
    cfg["mcpServers"][sid] = spec
    try:
        _write_config(cfg)
    except Exception as e:
        return False, f"could not write config.json: {e}"
    return True, None


def config_remove_server(server_id):
    """Remove a server from config.json. Returns (ok, error)."""
    sid = (server_id or "").strip()
    cfg = _read_config()
    if sid not in cfg.get("mcpServers", {}):
        return False, f"server '{sid}' is not in the config"
    del cfg["mcpServers"][sid]
    try:
        _write_config(cfg)
    except Exception as e:
        return False, f"could not write config.json: {e}"
    return True, None


def restart_self():
    """Replace this process with a fresh one so config.json is reloaded from
    scratch. Children are killed first to free their stdio pipes / ports before
    the new instance claims them. Never returns on success (os.execv)."""
    log("restarting bridge to load new server config...", "yl")
    try:
        for c in mgr.clients.values():
            c.stop()
    except Exception:
        pass
    if _log_file:
        try:
            _log_file.flush()
        except Exception:
            pass
    # sys.argv[0] may be relative ('bridge.py'); make it absolute so the restart
    # works regardless of the current working directory.
    argv = list(sys.argv)
    script = os.path.abspath(argv[0]) if argv else os.path.abspath(__file__)
    argv = [script] + argv[1:]
    try:
        os.execv(sys.executable, [sys.executable] + argv)
    except Exception as e:
        # execv failed (rare) - fall back to spawning a detached copy and exiting
        # so the user still ends up with a running, up-to-date bridge.
        log(f"in-place restart failed ({e}); spawning a fresh bridge...", "rd")
        try:
            subprocess.Popen([sys.executable] + argv, cwd=HERE)
        except Exception as e2:
            log(f"could not spawn a fresh bridge: {e2} - please restart it manually", "rd")
        os._exit(0)


# ══════════════════════════════════════════════════════════════════════════
#  HARDENED MCP CLIENT  (one per server in config.json)
# ══════════════════════════════════════════════════════════════════════════
class MCPClient:
    def __init__(self, server_id, command, args, env=None):
        self.id = server_id
        self.command = command
        self.args = list(args or [])
        self.env = env or {}
        self.proc = None
        self.req_id = 1
        self.write_lock = threading.Lock()
        self.call_lock = threading.Lock()   # serialize tool calls (single stdio pipe)
        self.pending = {}                    # id -> queue.Queue (one slot)
        self.pend_lock = threading.Lock()
        self.tools_cache = []
        self.start_lock = threading.Lock()
        self._reader_thread = None
        # Crash-loop forensics (read by server_watch). The auto-restart used to
        # hide a server that something else kills over and over: the terminal
        # showed an endless quiet restart cycle with no explanation at all. We
        # keep just enough state to NAME the problem in the terminal instead:
        #  - last_exit: exit code from the final _reader EOF (crash vs kill hint)
        #  - stderr_tail: the last few stderr lines (usually the actual reason -
        #    port bind failure, missing dependency, crash trace)
        #  - restart_times: recent auto-restart timestamps (loop detector input)
        #  - loop_warned_at: throttle so the big red banner prints once per
        #    cooldown, not every 5s poll
        self.last_exit = None
        self.stderr_tail = []
        self.restart_times = []
        self.loop_warned_at = 0.0
        # Set when the configured command itself couldn't be launched at all
        # (e.g. 'uvx' not installed / not on PATH). This is NOT a crash - the
        # process never existed, so last_exit/stderr_tail stay empty and the
        # generic crash-loop banner used to print "the server printed no error
        # output before dying", which is misleading for a config problem the
        # user can fix in seconds. Kept across restarts so the banner can name
        # the real cause instead.
        self.start_error = None
    # ── lifecycle ─────────────────────────────────────────────────────────
    def _resolve(self, s):
        return os.path.expandvars(os.path.expanduser(str(s)))

    def start(self):
        with self.start_lock:
            if self.is_alive():
                return
            cmd = [self._resolve(self.command)] + [self._resolve(a) for a in self.args]
            # A bare .py command (relative paths resolve against the bridge dir)
            # is run with the SAME interpreter the bridge itself uses, so it works
            # even on installs where only the `py` launcher exists (no `python`
            # on PATH). This is how local .py MCP servers are wired.
            if cmd[0].lower().endswith(".py"):
                script = cmd[0]
                if not os.path.isabs(script):
                    script = os.path.join(HERE, script)
                cmd = [sys.executable, script] + cmd[1:]
            # On Windows, npx/npm/yarn/pnpm/bunx are .cmd shims that Popen can't
            # launch directly (WinError 2). Run them through cmd.exe so any
            # node-based MCP server "just works" from config.json.
            if sys.platform == "win32":
                base = os.path.basename(cmd[0]).lower()
                if base in ("npx", "npm", "yarn", "pnpm", "bunx"):
                    cmd = ["cmd.exe", "/c"] + cmd
            env = dict(os.environ)
            for k, v in self.env.items():
                env[k] = self._resolve(v)
            log(f"[{self.id}] launching  ({' '.join(cmd)})", "cy")
            with _Spinner(f"    [{self.id}] starting..."):
                try:
                    self.proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                        encoding="utf-8",
                        errors="replace",
                        cwd=HERE,
                        env=env,
                    )
                except FileNotFoundError:
                    # The OS couldn't find cmd[0] at all - this is a config
                    # problem (missing dependency, typo, not on PATH), not a
                    # transient crash. Auto-restart will keep retrying (the
                    # user may install it later), but name the real cause so
                    # it doesn't just look like an endless silent restart loop.
                    self.start_error = (
                        f"command not found: '{cmd[0]}' - is it installed and on PATH? "
                        f"(configured for server '{self.id}' in config.json)"
                    )
                    log(f"[{self.id}] {self.start_error}", "rd")
                    raise
                except OSError as e:
                    self.start_error = f"could not launch '{cmd[0]}': {e}"
                    log(f"[{self.id}] {self.start_error}", "rd")
                    raise
                else:
                    self.start_error = None
                with self.pend_lock:
                    self.pending.clear()
                self._reader_thread = threading.Thread(target=self._reader, args=(self.proc,), daemon=True)
                self._reader_thread.start()
                threading.Thread(target=self._stderr_drain, args=(self.proc,), daemon=True).start()

                # MCP handshake.
                self._request("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "zeroscript-bridge", "version": "1.0"},
                }, timeout=30)
                self._notify("notifications/initialized")
                # Some MCP servers advertise 0 tools at the instant initialize
                # returns, because they connect to their backend a moment AFTER
                # the stdio handshake. A single tools/list then caches an empty
                # list forever. So if we get nothing, retry for a few seconds to
                # let the backend attach. Short per-attempt timeout so the bridge
                # never looks frozen if the server stays silent (e.g. its app not
                # open yet); ~12s total budget.
                for _ in range(12):
                    if self.refresh_tools(timeout=3):
                        break
                    if not self.is_alive():
                        break
                    time.sleep(1.0)
            log(f"[{self.id}] MCP server up  ({len(self.tools_cache)} tools advertised)", "cy")

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def restart(self):
        log(f"[{self.id}] restarting...", "yl")
        self.stop()
        time.sleep(0.4)
        self.start()

    def stop(self):
        with self.pend_lock:
            for q in self.pending.values():
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
            self.pending.clear()
        if self.proc:
            # proc.terminate() (TerminateProcess on Windows) only kills THIS
            # pid. Our command is often a wrapper that Popen()s a real child to
            # own the stdio pipes - terminate() would leave that child orphaned,
            # fighting the next restart's fresh instance. taskkill /T kills the
            # whole tree.
            try:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                        capture_output=True, timeout=8,
                    )
                else:
                    self.proc.terminate()
            except Exception:
                pass
        self.proc = None

    # ── io threads ────────────────────────────────────────────────────────
    def _reader(self, proc):
        stream = proc.stdout
        while True:
            try:
                line = stream.readline()
            except Exception:
                break
            if line == "":  # EOF -> process exited
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue  # stray non-JSON log on stdout
            mid = msg.get("id")
            if mid is None:
                continue  # server notification, nothing waits on it
            with self.pend_lock:
                q = self.pending.get(mid)
            if q is not None:
                try:
                    q.put_nowait(msg)
                except Exception:
                    pass
        code = proc.poll()
        self.last_exit = code  # kept for the crash-loop banner in server_watch
        log(f"[{self.id}] stdout closed (process ended, exit code {code})", "rd")
        with self.pend_lock:
            for q in self.pending.values():
                try:
                    q.put_nowait(None)
                except Exception:
                    pass

    def _stderr_drain(self, proc):
        # Surface the child's stderr instead of silently discarding it - this
        # is often the ONLY clue why a server died (crash trace, port bind
        # failure, missing app, etc).
        try:
            for line in iter(proc.stderr.readline, ""):
                line = line.rstrip()
                if line:
                    # Ring buffer of the last stderr lines: when the server
                    # enters a crash loop, these are printed in the terminal
                    # banner - they are usually the only real explanation
                    # (port already in use, module not found, crash trace).
                    self.stderr_tail.append(line)
                    if len(self.stderr_tail) > 8:
                        self.stderr_tail.pop(0)
                    log(f"[{self.id}] stderr: {line}", "yl", terminal=False)
        except Exception:
            pass

    # ── jsonrpc ───────────────────────────────────────────────────────────
    def _next_id(self):
        with self.write_lock:
            rid = self.req_id
            self.req_id += 1
            return rid

    def _notify(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        with self.write_lock:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()

    def _request(self, method, params, timeout):
        if not self.is_alive():
            raise RuntimeError(f"server '{self.id}' is not running")
        rid = self._next_id()
        q = queue.Queue(maxsize=1)
        with self.pend_lock:
            self.pending[rid] = q
        try:
            payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
            with self.write_lock:
                self.proc.stdin.write(json.dumps(payload) + "\n")
                self.proc.stdin.flush()
            try:
                return q.get(timeout=timeout)
            except queue.Empty:
                return None
        finally:
            with self.pend_lock:
                self.pending.pop(rid, None)

    # ── high-level ────────────────────────────────────────────────────────
    def refresh_tools(self, timeout=20):
        msg = self._request("tools/list", {}, timeout=timeout)
        if msg and "result" in msg:
            self.tools_cache = msg["result"].get("tools", [])
        return self.tools_cache

    def call_tool(self, name, arguments, timeout):
        """Returns {"text":..., "images":[...]}. Raises on error/timeout."""
        with self.call_lock:
            for attempt in (1, 2):
                if not self.is_alive():
                    self.restart()
                msg = self._request("tools/call",
                                    {"name": name, "arguments": arguments}, timeout)
                if msg is None:
                    if not self.is_alive():
                        self.restart()
                        msg = self._request("tools/call",
                                            {"name": name, "arguments": arguments}, timeout)
                    if msg is None:
                        raise TimeoutError(
                            f"No response from server '{self.id}' after {timeout}s.")
                if msg.get("error"):
                    err = msg["error"]
                    err_text = err.get("message", json.dumps(err))
                    raise RuntimeError(err_text)
                content = msg.get("result", {}).get("content", [])
                text = "\n".join(it.get("text", "") for it in content if it.get("type") == "text")
                images = [{"data": it["data"], "mimeType": it.get("mimeType", "image/jpeg")}
                          for it in content if it.get("type") == "image" and it.get("data")]
                if not text and not images and content:
                    text = json.dumps(content)[:4000]
                # MCP tool-level failure: the server answered, but the tool
                # itself reported an error (isError). Standard reference servers
                # (filesystem, git, ...) use this for bad paths, missing files,
                # etc. Surface it as a real error - otherwise the extension
                # shows a green chip and the model reads "Output of 'x':
                # error: ..." as if it were tool output.
                if msg.get("result", {}).get("isError"):
                    raise RuntimeError(text or "the tool reported an error")
                return {"text": text, "images": images}


# ══════════════════════════════════════════════════════════════════════════
#  MANAGER  - aggregates every MCP server, routes by tool name.
# ══════════════════════════════════════════════════════════════════════════
class MCPManager:
    def __init__(self):
        self.clients = {}          # server_id -> MCPClient
        self.index = {}            # advertised_name -> (holder, real_name)
        self.index_lock = threading.Lock()

    def load_config(self):
        servers = _read_config().get("mcpServers", {}) or {}
        for sid, spec in servers.items():
            self.clients[sid] = MCPClient(
                sid, spec.get("command"), spec.get("args"), spec.get("env"))
        log(f"configured {len(self.clients)} MCP server(s): {', '.join(self.clients) or '(none)'}", "cy")

    def start_all(self):
        # Launch every configured server IN PARALLEL, not one after another.
        # client.start() can block for up to ~12s (its own "wait for tools to
        # appear" grace loop) - with a sequential for-loop, a slow first server
        # would hold up every other one even though they have nothing to do with
        # each other. A thread per client removes that dependency entirely.
        threads = []
        for sid, client in self.clients.items():
            def _run(sid=sid, client=client):
                try:
                    client.start()
                except Exception as e:
                    log(f"[{sid}] failed to start: {e}  (other servers continue)", "rd")
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        self.rebuild_index()

    def rebuild_index(self):
        """Aggregate server tools. Collisions get a 'server/' prefix."""
        with self.index_lock:
            self.index = {}
            for sid, client in self.clients.items():
                for t in (client.tools_cache or []):
                    name = t.get("name")
                    if not name:
                        continue
                    advertised = name if name not in self.index else f"{sid}/{name}"
                    self.index[advertised] = (client, name)

    def list_tools(self, refresh=False):
        if refresh:
            for sid, client in self.clients.items():
                try:
                    if not client.is_alive():
                        client.start()
                    else:
                        client.refresh_tools()
                except Exception as e:
                    log(f"[{sid}] refresh failed: {e}", "yl")
            self.rebuild_index()
        out = []
        for sid, client in self.clients.items():
            for t in (client.tools_cache or []):
                name = t.get("name")
                advertised = name
                with self.index_lock:
                    # find the advertised key that maps to this (client, name)
                    for k, (holder, real) in self.index.items():
                        if holder is client and real == name:
                            advertised = k
                            break
                tt = dict(t)
                tt["name"] = advertised
                tt["server"] = sid
                out.append(tt)
        return out

    def call(self, name, arguments, timeout):
        with self.index_lock:
            entry = self.index.get(name)
        if entry is None:
            # Maybe a freshly added tool - rebuild once and retry.
            self.rebuild_index()
            with self.index_lock:
                entry = self.index.get(name)
        if entry is None:
            raise RuntimeError(f"unknown tool '{name}'")
        holder, real_name = entry
        return holder.call_tool(real_name, arguments, timeout)

    def restart(self, server_id=None):
        targets = [self.clients[server_id]] if server_id and server_id in self.clients else list(self.clients.values())
        for client in targets:
            try:
                client.restart()
            except Exception as e:
                log(f"[{client.id}] restart failed: {e}", "rd")
        self.rebuild_index()

    def health(self):
        return [{"id": sid, "alive": c.is_alive(), "tools": len(c.tools_cache)}
                for sid, c in self.clients.items()]

    def any_alive(self):
        return any(c.is_alive() for c in self.clients.values())


# ══════════════════════════════════════════════════════════════════════════
#  WEBSOCKET SERVER
# ══════════════════════════════════════════════════════════════════════════
mgr = MCPManager()
clients = set()

def safe_call(name, arguments, timeout):
    """Never raises. Always returns a dict the extension can feed back to DeepSeek."""
    try:
        result = mgr.call(name, arguments, timeout)
        return {"ok": True, "text": result["text"], "images": result["images"]}
    except TimeoutError as e:
        return {"ok": False, "error": str(e), "kind": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e), "kind": type(e).__name__}


async def run_tool_task(ws, name, args, timeout, rid):
    """Execute one tool off the socket read loop and send its result back.

    Kept as a standalone task (not awaited inline in handler) so a long tool
    never starves the connection's ability to answer app-level pings - see the
    call_tool branch in handler() for the full rationale."""
    t0 = time.monotonic()
    res = await asyncio.to_thread(safe_call, name, args, timeout)
    elapsed = time.monotonic() - t0
    tag = "gr" if res.get("ok") else "rd"
    summary = (res.get("text") or res.get("error") or "")[:80].replace("\n", " ")
    slow = "  [SLOW]" if elapsed > 5 else ""
    # Routine per-call traces are technical noise for a non-dev user watching
    # the console; they still land in bridge_debug.log. A failed/slow call
    # DOES surface on the terminal - that's the signal a user should notice.
    log(f"<- {name} ({elapsed:.1f}s){slow}: {summary}", tag, terminal=not res.get("ok") or elapsed > 5)
    try:
        await ws.send(json.dumps({"type": "tool_result", "id": rid, **res}))
    except websockets.ConnectionClosed:
        pass


async def broadcast_status():
    """Push a fresh status snapshot to every currently-connected extension tab.

    Needed because the socket now starts listening (see _boot_and_diagnose in
    main()) before every MCP server has necessarily finished launching in the
    background - an extension that connects in that window gets an early,
    incomplete "connected" snapshot (e.g. an addon server not started yet).
    The extension's own periodic poll only reads a passively cached copy of
    the LAST message it received (background.js never re-probes on its own),
    so without a follow-up push that stale snapshot can persist forever (seen
    live 2026-07-11: a server not yet alive at connect-time froze the Start
    button in its fully-disabled, non-degraded state even long after the
    server was actually up). background.js already handles a second
    "connected" message arriving at any time (updates its cache and re-renders
    the bar), so re-sending this exact shape once startup truly settles is
    enough to self-correct with zero extension-side changes needed.
    """
    if not clients:
        return
    try:
        payload = json.dumps({
            "type": "connected",
            "mcp_alive": mgr.any_alive(),
            "servers": mgr.health(),
            "tools": mgr.list_tools(),
            "port": PORT,
        })
    except Exception:
        return
    for ws in list(clients):
        try:
            await ws.send(payload)
        except Exception:
            pass


async def handler(ws):
    peer = getattr(ws, "remote_address", ("?",))[0]
    clients.add(ws)
    log(f"extension connected  ({peer})  [{len(clients)} client(s)]", "gr")
    try:
        await ws.send(json.dumps({
            "type": "connected",
            "mcp_alive": mgr.any_alive(),
            "servers": mgr.health(),
            "tools": mgr.list_tools(),
            "port": PORT,
        }))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            rid = msg.get("id")

            if mtype == "ping":
                await ws.send(json.dumps({"type": "pong", "id": rid}))

            elif mtype == "list_tools":
                try:
                    tools = await asyncio.to_thread(mgr.list_tools, True)
                except Exception as e:
                    tools = mgr.list_tools()
                    log(f"list_tools error: {e}", "yl")
                await ws.send(json.dumps({
                    "type": "tools", "id": rid,
                    "tools": tools, "mcp_alive": mgr.any_alive(),
                    "servers": mgr.health(),
                }))

            elif mtype == "call_tool":
                name = msg.get("name", "")
                args = msg.get("arguments") or {}
                timeout = float(msg.get("timeout", 120000)) / 1000.0
                log(f"-> tool  {name}({', '.join(args.keys())})", "cy", terminal=False)
                # Run the tool as a BACKGROUND task instead of awaiting it here.
                # Awaiting inline parks this read loop for the WHOLE tool call, so
                # a long tool (e.g. wait_job_finished > 25s) means the client's
                # app-level pings are never read/answered - its half-open-socket
                # watchdog then force-closes the connection and the in-flight call
                # is dropped as "bridge unreachable" (reported live). As a task,
                # the loop stays free to answer pings/status while the tool runs.
                # The extension only ever has ONE call_tool in flight (its agent
                # loop awaits each result before sending the next), so this never
                # overlaps tool executions.
                asyncio.create_task(run_tool_task(ws, name, args, timeout, rid))

            elif mtype in ("add_server", "remove_server"):
                # Adding/removing an MCP server rewrites config.json, which the
                # bridge only reads at launch - so we ack, then restart the
                # whole process to pick it up cleanly.
                if mtype == "add_server":
                    ok, err = await asyncio.to_thread(
                        config_add_server,
                        msg.get("server_id"), msg.get("command"),
                        msg.get("args"), msg.get("env"))
                else:
                    ok, err = await asyncio.to_thread(
                        config_remove_server, msg.get("server_id"))
                await ws.send(json.dumps({
                    "type": "server_changed", "id": rid,
                    "ok": ok, "error": err, "restarting": ok,
                }))
                if ok:
                    # Give the ack a beat to flush over the socket, then restart.
                    async def _do_restart():
                        await asyncio.sleep(0.4)
                        restart_self()
                    asyncio.create_task(_do_restart())

            elif mtype == "restart_mcp":
                sid = msg.get("server")
                try:
                    await asyncio.to_thread(mgr.restart, sid)
                    ok, err = True, None
                except Exception as e:
                    ok, err = False, str(e)
                await ws.send(json.dumps({
                    "type": "mcp_status", "id": rid,
                    "alive": mgr.any_alive(), "ok": ok, "error": err,
                    "servers": mgr.health(), "tools": mgr.list_tools(),
                }))

            else:
                await ws.send(json.dumps({
                    "type": "error", "id": rid,
                    "error": f"unknown message type: {mtype}",
                }))
    except websockets.ConnectionClosed:
        pass
    except Exception as e:
        log(f"handler error: {e}", "rd")
    finally:
        clients.discard(ws)
        log(f"extension disconnected  [{len(clients)} client(s)]", "yl")


async def server_watch():
    """Poll every MCP server and restart any that died unexpectedly (e.g. a
    wrapper proxy crashing on its own - see stop()'s taskkill /T fix and the
    stderr logging above for why this used to happen silently). Without this,
    a dead server only got noticed on the NEXT real tool call, which is what
    made "a server looks connected but nothing responds" possible."""
    # Crash-LOOP detection thresholds: LOOP_N deaths within LOOP_WINDOW seconds
    # means something is killing (or instantly crashing) the server every time
    # we bring it back - the silent restart cycle the auto-restart otherwise
    # hides completely. We still keep restarting (the cause may be transient,
    # e.g. the user is about to start Blender), but the terminal now NAMES the
    # problem: exit code, the child's last stderr lines, and - for a port-bound
    # server - who is squatting the port. Banner re-prints at most every
    # LOOP_WARN_COOLDOWN so the terminal stays readable.
    LOOP_N = 3
    LOOP_WINDOW = 60
    LOOP_WARN_COOLDOWN = 120
    while True:
        await asyncio.sleep(5)
        for sid, client in list(mgr.clients.items()):
            try:
                if not client.is_alive():
                    now = time.time()
                    # restart_times holds RESTART ATTEMPTS (appended just before
                    # each start below), never per-poll sightings - appending on
                    # every 5s poll would keep the window full forever and the
                    # "slow down" branch would then block restarts permanently.
                    client.restart_times = [t for t in client.restart_times if now - t < LOOP_WINDOW]
                    looping = len(client.restart_times) >= LOOP_N
                    if looping and now - client.loop_warned_at > LOOP_WARN_COOLDOWN:
                        client.loop_warned_at = now
                        log(f"[{sid}] CRASH LOOP: died {len(client.restart_times)} times in the last "
                            f"{LOOP_WINDOW}s (last exit code: {client.last_exit}). Something is killing it "
                            f"or it cannot start.", "rd")
                        if client.start_error:
                            log(f"[{sid}] {client.start_error}", "rd")
                        elif client.stderr_tail:
                            log(f"[{sid}] last error output (usually the real reason):", "rd")
                            for ln in client.stderr_tail:
                                log(f"[{sid}]   {ln}", "yl")
                        else:
                            log(f"[{sid}] the server printed no error output before dying.", "yl")
                        log(f"[{sid}] common causes: its app is not running (e.g. Blender + addon), a port "
                            f"conflict, an antivirus killing it, or a bad command in config.json. "
                            f"Auto-restart continues in the background.", "yl")
                    if looping and client.restart_times and now - client.restart_times[-1] < 15:
                        # Clearly hopeless right now: drop to a ~15s cadence so a
                        # broken command isn't hammer-spawned every 5 seconds,
                        # while still retrying forever (the cause may clear, e.g.
                        # the user finally opens Blender).
                        continue
                    client.restart_times.append(now)
                    log(f"[{sid}] found dead - auto-restarting...", "yl")
                    await asyncio.to_thread(client.start)
                    mgr.rebuild_index()
                    await broadcast_status()  # tell any connected extension right away
            except Exception as e:
                log(f"[{sid}] auto-restart failed: {e}", "rd")


async def _supervised(name, coro_factory):
    """Run a watcher coroutine forever, restarting it if it ever raises.

    Watchers are designed to never raise, but one line proved that wrong in
    practice (an UnboundLocalError killed one watcher SILENTLY - asyncio only
    prints 'Task exception was never retrieved' at shutdown, so all monitoring
    and status broadcasts just stopped until the user restarted the bridge).
    A crash in a watcher must never be silent or permanent: log it loudly,
    wait a beat, start a fresh instance.
    """
    while True:
        try:
            await coro_factory()
            return  # normal completion (doesn't happen today, but respect it)
        except Exception as e:
            log(f"{name} crashed: {type(e).__name__}: {e} - restarting it in 5s "
                f"(please report this).", "rd")
            await asyncio.sleep(5)


# ══════════════════════════════════════════════════════════════════════════
#  HTTP (Streamable HTTP) MCP FACE - for EXTERNAL MCP clients
# ══════════════════════════════════════════════════════════════════════════
#  The browser extension talks to this bridge over WebSocket. But other MCP
#  clients - arena.ai's Agent Mode, Claude Desktop, Cursor, any MCP-capable
#  app - speak MCP over HTTP (Streamable HTTP transport: JSON-RPC in a POST
#  body, Mcp-Session-Id header, JSON back). This face exposes the SAME server
#  catalogue and tool routing to them, so one local bridge serves every
#  client:
#
#    Bridge (this) ──cloudflared tunnel──> https://xxx.trycloudflare.com/mcp/<token>
#        └── paste that URL into arena.ai Agent Mode / any MCP client
#
#  Security model:
#   - Binds 127.0.0.1 ONLY. Reaching it from another machine requires a
#     tunnel the user starts on purpose - that act is what makes the URL
#     "the" credential, so the path carries a per-launch random token.
#   - The full URL is printed at boot AND written to mcp_http_url.txt next
#     to bridge.py (0600) so the tunnel/client side can read it without
#     mining console scrollback.
#   - Every tools/call goes through the exact same per-server lock, timeout
#     and error shaping as the WebSocket path, so the agentic-loop
#     invariants (always a reply, never a hang) hold unchanged.
MCP_HTTP_PORT = int(os.environ.get("ZS_MCP_HTTP_PORT", "17614"))
MCP_HTTP_TOKEN = None          # set at boot; None = face disabled
MCP_HTTP_URL_FILE = os.path.join(HERE, "mcp_http_url.txt")
_http_sessions = set()
MCP_PROTOCOL_VERSION = "2025-06-18"


def _mcp_dispatch(method, params):
    """One JSON-RPC method for the HTTP face. Returns (http_status, result,
    is_new_session). Tool-level failures come back as MCP isError results
    (a tool failing is NOT a protocol error); unknown methods raise."""
    if method == "initialize":
        # Echo the client's protocol version when it looks like one (versions
        # only ever gain capabilities), else answer with ours.
        client_v = str((params or {}).get("protocolVersion") or "")
        version = client_v if (client_v and client_v[0].isdigit()) else MCP_PROTOCOL_VERSION
        return 200, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "zeroscript-bridge", "version": BRIDGE_VERSION},
        }, True
    if method == "tools/list":
        # Same catalogue the WebSocket side advertises (per-server tools,
        # collision-prefixed names). inputSchema is already MCP-shaped.
        return 200, {"tools": mgr.list_tools()}, False
    if method == "tools/call":
        name = str((params or {}).get("name") or "")
        args = (params or {}).get("arguments") or {}
        try:
            res = mgr.call(name, args, timeout=120)
        except Exception as e:
            return 200, {"content": [{"type": "text", "text": str(e)}],
                         "isError": True}, False
        content = [{"type": "text", "text": res.get("text") or "(empty result)"}]
        content += [{"type": "image", "data": img["data"], "mimeType": img["mimeType"]}
                    for img in (res.get("images") or [])]
        return 200, {"content": content, "isError": False}, False
    if method == "ping":
        return 200, {}, False
    raise KeyError(method)


class _McpHttpHandler(http.server.BaseHTTPRequestHandler):
    server_version = "ZeroScriptBridge/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # per-request logging would drown the boot banner; failures are
        # still visible in the bridge's own tool-call trace (run_tool_task).

    def _authed(self):
        parts = self.path.split("?", 1)[0].split("/")
        # expect exactly /mcp/<token>
        return (MCP_HTTP_TOKEN is not None and len(parts) == 3
                and parts[0] == "" and parts[1] == "mcp"
                and parts[2] == MCP_HTTP_TOKEN)

    def _send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._authed():
            # 404 (not 403) so a scanner doesn't learn the endpoint exists.
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            msg = json.loads(self.rfile.read(length) or b"null")
        except Exception:
            self._send_json(400, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700, "message": "parse error"}})
            return
        if not isinstance(msg, dict):
            self._send_json(400, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32600, "message": "invalid request"}})
            return
        rid = msg.get("id")
        method = msg.get("method")
        # Notifications (no id) need no body.
        if rid is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        sid = self.headers.get("Mcp-Session-Id")
        try:
            status, result, new_session = _mcp_dispatch(method, msg.get("params"))
        except KeyError:
            self._send_json(200, {"jsonrpc": "2.0", "id": rid,
                                  "error": {"code": -32601,
                                            "message": "method not found: %s" % method}})
            return
        headers = None
        if new_session:
            new_sid = uuid.uuid4().hex
            _http_sessions.add(new_sid)
            headers = {"Mcp-Session-Id": new_sid}
        elif sid:
            # Spec clients echo the id back. Validate it so a stale/replayed
            # session from a previous bridge launch is rejected (the client
            # then re-initializes, per the spec's recovery flow). Lenient when
            # the header is ABSENT - naive clients (hand-rolled curl) often
            # omit it, and this is a local utility, not a multi-tenant API.
            if sid not in _http_sessions:
                self._send_json(400, {"jsonrpc": "2.0", "id": rid,
                                      "error": {"code": -32600,
                                                "message": "invalid or expired Mcp-Session-Id - send initialize again"}})
                return
        self._send_json(status, {"jsonrpc": "2.0", "id": rid, "result": result}, headers)

    def do_GET(self):
        # Streamable HTTP clients may open the SSE stream for server pushes.
        # The bridge never pushes (clients poll via tools), so 405 with the
        # allowed methods is a spec-permitted answer.
        if self._authed():
            self.send_response(405)
            self.send_header("Allow", "POST, DELETE")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send_json(404, {"error": "not found"})

    def do_DELETE(self):
        if self._authed():
            sid = self.headers.get("Mcp-Session-Id")
            if sid:
                _http_sessions.discard(sid)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def start_mcp_http():
    """Start the HTTP MCP face. Returns the full URL, or None when disabled
    (ZS_MCP_HTTP_PORT=0) or the port is already taken (the WebSocket face is
    primary - the bridge must still boot without this)."""
    global MCP_HTTP_TOKEN
    if MCP_HTTP_PORT <= 0:
        return None
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", MCP_HTTP_PORT), _McpHttpHandler)
    except OSError as e:
        log(f"HTTP MCP face not started: port {MCP_HTTP_PORT} unavailable ({e}) - "
            "set ZS_MCP_HTTP_PORT to another value (0 disables it).", "yl")
        return None
    MCP_HTTP_TOKEN = uuid.uuid4().hex
    threading.Thread(target=server.serve_forever, daemon=True, name="mcp-http").start()
    url = f"http://127.0.0.1:{MCP_HTTP_PORT}/mcp/{MCP_HTTP_TOKEN}"
    try:
        fd = os.open(MCP_HTTP_URL_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(url + "\n")
    except Exception:
        pass  # the console line below is the source of truth
    return url


async def main():
    mgr.load_config()
    log(f"===== BRIDGE START  v{BRIDGE_VERSION}  pid={os.getpid()}  log={LOG_PATH} =====", "cy")
    print(f"\n{C['cy']}  ZeroScript Bridge v{BRIDGE_VERSION}{C['reset']}  {C['dim']}- "
          f"local MCP bridge - ws://{HOST}:{PORT}{C['reset']}\n")

    async def _boot_and_diagnose():
        """Launch every configured MCP server and print the boot diagnostic
        banner. Runs as a background task AFTER the socket below is already
        listening, so a slow server never delays the extension's ability to
        connect right away - only the terminal banner waits on this.
        (mgr.start_all() itself also launches every server in parallel.)"""
        try:
            await asyncio.to_thread(mgr.start_all)
        except Exception as e:
            log(f"server startup error: {e}", "rd")
            log("The bridge will keep running; it retries on the first tool call.", "yl")
        total = len(mgr.list_tools())
        if total == 0:
            if mgr.clients:
                log("ready - 0 tools available.", "yl")
                log("    Check each server's command in the extension's menu (MCP servers)", "yl")
                log("    (or config.json). A dead server is auto-restarted, so an app that", "yl")
                log("    starts up later will connect on its own.", "yl")
            else:
                log("ready - no MCP servers are configured yet.", "yl")
                log("    Add one in the extension's MCP servers menu (or edit config.json) -", "yl")
                log("    the bridge picks it up on its next restart.", "yl")
        else:
            log(f"ready {total} tools available ({len(mgr.clients)} MCP server(s))", "gr")

    async def _early_status_pushes():
        """A few follow-up status broadcasts shortly after boot.

        mgr.start_all() still doesn't RETURN until every server's thread has
        joined - including a slow one (its own "waiting for tools" retry loop,
        seen live). So a single broadcast placed after start_all() would be
        just as slow as the old blocking behavior for the exact case this is
        meant to fix: a server that's ready in 1-13s while another is still
        slowly timing out. Poll-and-broadcast a few times instead, cheaply,
        so any extension that connected during that window self-corrects
        quickly instead of staying stuck on its first, incomplete snapshot.
        """
        for interval in (2, 2, 4, 6, 6):  # cumulative: 2s, 4s, 8s, 14s, 20s after boot
            await asyncio.sleep(interval)
            await broadcast_status()

    # Free our own port from a leftover bridge (double-launch / X-closed window /
    # prior crash) BEFORE binding, so relaunching start.bat "just works" instead
    # of dying on WinError 10048. Only ever kills a proven bridge.py; anything
    # else falls through to the friendly bind-error below.
    if await asyncio.to_thread(_reclaim_bridge_port):
        await asyncio.sleep(0.6)  # let Windows release the socket before we bind

    try:
        server_ctx = await websockets.serve(
            handler, HOST, PORT, ping_interval=20, ping_timeout=20,
            max_size=16 * 1024 * 1024)
    except OSError as e:
        # errno 10048 (Win) / EADDRINUSE: something we could NOT auto-kill still
        # owns the port - another app, or a python whose cmdline we couldn't read.
        if getattr(e, "errno", None) in (98, 10048) or "10048" in str(e):
            owner = await asyncio.to_thread(_port_owner, PORT)
            who = f" by '{owner[1]}' (pid {owner[0]})" if owner else ""
            log(f"could not start: port {PORT} is already in use{who}.", "rd")
            log(f"    A previous bridge may still be running, or another app took "
                f"the port. Close it, then relaunch. To find it:", "yl")
            log(f"      netstat -ano | findstr {PORT}", "yl")
            log(f"      taskkill /F /PID <the pid from the last column>", "yl")
            log(f"    Or set a different port before start.bat:  set ZS_BRIDGE_PORT=17614", "yl")
            return
        raise

    # External-MCP-client face (arena Agent Mode, Claude Desktop, Cursor...):
    # an HTTP Streamable-HTTP endpoint mirroring the same tools, token-gated
    # and localhost-only. Best-effort - a dead port never blocks the WS face.
    mcp_http_url = await asyncio.to_thread(start_mcp_http)
    if mcp_http_url:
        log(f"HTTP MCP endpoint (external clients): {mcp_http_url}", "cy")
        log(f"    for another machine: run a tunnel, e.g. `cloudflared tunnel --url "
            f"http://127.0.0.1:{MCP_HTTP_PORT}` and paste the public URL + path. "
            "Treat the URL as a secret.", "dim")

    async with server_ctx:
        log(f"listening on ws://{HOST}:{PORT}  - load the extension and open a supported AI chat", "cy")
        asyncio.create_task(_supervised("server_watch", server_watch))
        asyncio.create_task(_boot_and_diagnose())
        asyncio.create_task(_early_status_pushes())
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("shutting down...", "yl")
        for c in mgr.clients.values():
            c.stop()
    finally:
        log("===== BRIDGE STOP =====", "cy")
