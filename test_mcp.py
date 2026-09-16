# SPDX-License-Identifier: GPL-3.0-or-later
# test_mcp.py
# ──────────────────────────────────────────────────────────────────────────
#  A minimal FILESYSTEM MCP server for testing ZeroScript in GENERIC (non
#  Roblox) mode. No dependencies - it runs on the same Python the bridge
#  uses. NOT part of the product; it exists so you can verify the generic
#  MCP flow end-to-end without installing any external MCP server.
#
#  Usage: in config.json next to bridge.py (Windows example):
#
#    {
#      "mcpServers": {
#        "testfs": { "command": "test_mcp.py", "args": ["D:/zeroscript-test"] }
#      }
#    }
#
#  Remove the "roblox" entry (or delete it from the extension's ⋯ menu →
#  MCP servers) to test fully generic mode, then run start.bat /
#  MacOS_Start.command as usual.
#
#  Exposed tools (rooted at the directory you pass, no escapes):
#    list_files(path?)  - list a directory inside the root
#    read_file(path)    - read a text file
#    write_file(path, content) - create/overwrite a text file
# ──────────────────────────────────────────────────────────────────────────
import json
import os
import sys

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()

TOOLS = [
    {
        "name": "list_files",
        "description": f"List the files and folders inside the test directory {ROOT}. "
                       "Optional 'path' is a sub-path relative to the root (directories end with '/').",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
    {
        "name": "read_file",
        "description": f"Read a text file under {ROOT}. 'path' is relative to the root.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                        "required": ["path"]},
    },
    {
        "name": "write_file",
        "description": f"Create or overwrite a text file under {ROOT}. 'path' is relative "
                       "to the root; parent folders are created as needed.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"},
                                                         "content": {"type": "string"}},
                        "required": ["path", "content"]},
    },
]


def _safe(rel):
    """Resolve `rel` inside ROOT; refuse anything that escapes it."""
    p = os.path.abspath(os.path.join(ROOT, rel or ""))
    if p != ROOT and not p.startswith(ROOT + os.sep):
        raise ValueError(f"path escapes the test root: {rel!r}")
    return p


def _call(name, args):
    if name == "list_files":
        p = _safe(args.get("path", ""))
        if not os.path.isdir(p):
            raise ValueError(f"not a directory: {args.get('path')!r}")
        entries = sorted(os.listdir(p))
        lines = []
        for e in entries:
            full = os.path.join(p, e)
            lines.append(e + "/" if os.path.isdir(full) else e)
        return "\n".join(lines) if lines else "(empty directory)"
    if name == "read_file":
        with open(_safe(args["path"]), encoding="utf-8", errors="replace") as f:
            return f.read()[:50000]
    if name == "write_file":
        p = _safe(args["path"])
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        content = args.get("content", "")
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return f"wrote {len(content)} chars to {p}"
    raise ValueError(f"unknown tool: {name!r}")


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "zeroscript-test-fs", "version": "1.0"},
            }})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            p = msg.get("params", {})
            try:
                text = _call(p.get("name"), p.get("arguments") or {})
                err = False
            except Exception as e:
                text = f"error: {e}"
                err = True
            _send({"jsonrpc": "2.0", "id": mid,
                   "result": {"content": [{"type": "text", "text": text}],
                              "isError": err}})
        # notifications (e.g. notifications/initialized) need no response


if __name__ == "__main__":
    main()
